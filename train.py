#!/usr/bin/env python3 -u
# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import argparse
import logging
from typing import Dict, Any, List, Optional
import math
import random
import os
import sys

# We need to setup root logger before importing any project libraries.
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level = os.environ.get("LOGLEVEL", "INFO").upper(),
    stream = sys.stdout
)
logger = logging.getLogger("train")

import numpy as np
import torch

import models
import utils
import checkpoint_utils
import data
from loggings import metrics, meters, progress_bar
from criterion import MultiTaskCriterion
from distributed import utils as distributed_utils
from trainer import Trainer

def get_parser():
    parser = argparse.ArgumentParser()

    # required arguments
    parser.add_argument(
        "--student_number",
        type=str,
        required=True,
        help="your student number"
    )
    parser.add_argument(
        "--data_path",
        type=str,
        required=True,
        help="path to your processed features, will be used in the dataset class"
    )

    # validation
    parser.add_argument(
        "--valid_percent",
        type=float,
        default=0.0,
        help="percentage for validation subset"
    )

    # optimizer
    parser.add_argument(
        "--lr",
        type=float,
        default=0.005,
        help="learning rate"
    )
    parser.add_argument(
        "--batch_size",
        type=int,
        default=64,
        help="batch size"
    )
    parser.add_argument(
        "--max_epoch",
        type=int,
        default=50,
        help="max epoch"
    )

    # adam optimizer
    parser.add_argument(
        "--adam_betas",
        type=str,
        default="(0.9, 0.999)",
        help="betas for Adam optimizer"
    )
    parser.add_argument(
        "--adam_eps",
        type=float,
        default=1e-8,
        help="epsilon for Adam optimizer"
    )
    parser.add_argument(
        "--weight_decay",
        type=float,
        default=0.0,
        help="weight decay"
    )

    # fixed lr scheduler
    parser.add_argument(
        "--force_anneal",
        type=int,
        default=None,
        help="force annealing at specified epoch"
    )
    parser.add_argument(
        "--lr_shrink",
        type=float,
        default=0.1,
        help="shrink factor for annealing, lr_new = (lr * lr_shrink)"
    )
    parser.add_argument(
        "--warmup_updates",
        type=int,
        default=0,
        help="warmup the learning rate linearly for the first N updates"
    )

    # training
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="random seed"
    )
    parser.add_argument(
        "--num_workers",
        type=int,
        default=6,
        help="num workers for loading batch"
    )
    parser.add_argument(
        "--pin_memory",
        type=bool,
        default=True,
        help="if True, the data loader will copy Tensors into device/CUDA pinned memory "
        + "before returning them."
    )
    parser.add_argument(
        "--clip_norm",
        type=float,
        default=0.0,
        help="clip threshold of gradients"
    )
    parser.add_argument(
        "--all_gather_list_size",
        default=1048576,
        help="number of bytes reserved for gathering stats from workers"
    )
    parser.add_argument(
        "--empty_cache_freq",
        type=int,
        default=0,
        help="how often to clear the PyTorch CUDA cache (0 to disable)"
    )

    # distributed training
    parser.add_argument(
        "--distributed_world_size",
        type=int,
        default=1,
        help="total number of GPUs across all nodes"
    )
    parser.add_argument(
        "--distributed_rank",
        type=int,
        default=0,
        help="rank of the current worker"
    )
    parser.add_argument(
        "--distributed_backend",
        type=str,
        default="nccl",
        help="distributed backend"
    )
    parser.add_argument(
        "--distributed_init_method",
        type=str,
        default=None,
        help="typically tcp://hostname:port that will be used to establish initial connection"
    )
    parser.add_argument(
        "--distributed_port",
        type=int,
        default=12355,
        help="port number"
    )
    parser.add_argument(
        "--device_id",
        type=int,
        default=0,
        help="which GPU to use"
    )
    parser.add_argument(
        "--ddp_comm_hook",
        type=str,
        default="none",
        help="communication hook"
    )
    parser.add_argument(
        "--bucket_cap_mb",
        type=int,
        default=25,
        help="bucket size for reduction"
    )
    parser.add_argument(
        "--find_unused_parameters",
        type=bool,
        default=False,
        help="disable unused parameter detection"
    )
    parser.add_argument(
        "--heartbeat_timeout",
        type=int,
        default=-1,
        help="kill the job if no progress is made in N seconds; set to -1 to disable"
    )
    parser.add_argument(
        "--broadcast_buffers",
        type=bool,
        default=False,
        help="Copy non-trainable parameters between GPUs, such as batchnorm population statistics"
    )
    
    # checkpoint
    parser.add_argument(
        "--save_dir",
        type=str,
        default="checkpoints",
        help="path to save checkpoints"
    )
    parser.add_argument(
        "--save_interval",
        type=int,
        default=1,
        help="save a checkpoint every N epochs"
    )
    parser.add_argument(
        "--no_save_optimizer_state",
        type=bool,
        default=False,
        help="don't save optimizer-state as part of checkpoint"
    )
    parser.add_argument(
        "--load_checkpoint_on_all_dp_ranks",
        type=bool,
        default=False,
        help="load checkpoints on all data parallel devices "
        + "(default: only load on rank 0 and broadcast to other devices)"
    )

    # logging
    parser.add_argument(
        "--log_interval",
        type=int,
        default=50,
        help="log interval"
    )

    # wandb logging
    parser.add_argument(
        "--wandb_project",
        type=str,
        default=None,
        help="wandb project name"
    )
    parser.add_argument(
        "--wandb_entity",
        type=str,
        default=None,
        help="wandb entity name"
    )
    
    return parser

def main(args: argparse.Namespace) -> None:
    metrics.reset()
    
    np.random.seed(args.seed)
    random.seed(args.seed)
    utils.set_torch_seed(args.seed)
    
    if distributed_utils.is_master(args):
        checkpoint_utils.verify_checkpoint_directory(args.save_dir)
    
    # Print args
    logger.info(args)
    
    # Build model
    model = models.build_model(args.student_number + "_model", **vars(args))
    criterion = MultiTaskCriterion.build_criterion(args)
    
    logger.info(model)
    logger.info("model: {}".format(model.__class__.__name__))

    # load dataset
    args.dataset = args.student_number + "_dataset"
    dataset = data.build_dataset(args)

    trainer = Trainer(args, model, criterion, train=dataset)

    breakpoint()
    logger.info(
        "training on {} devices (GPUs)".format(
            args.distributed_world_size
        )
    )

    max_epoch = args.max_epoch

    train_meter = meters.StopwatchMeter()
    train_meter.start()
    epoch_idx = 1
    while epoch_idx <= max_epoch:
        # train for one epoch
        valid_losses = train(args, trainer, epoch_idx)

        lr = trainer.lr_step(epoch_idx, valid_losses[0])
    
    train_meter.stop()
    logger.info("done training in {:.1f} seconds".format(train_meter.sum))

@metrics.aggregate("train")
def train(
    args: argparse.Namespace, trainer: Trainer, epoch: int
):
    """Train the model for one epoch and return validation losses."""
    progress = progress_bar.progress_bar(
        trainer.iterator,
        log_format="json",
        log_interval=args.log_interval,
        epoch=epoch,
        tensorboard_logdir=None,
        default_log_format=("tqdm"),
        wandb_project=(
            args.wandb_project
            if distributed_utils.is_master(args)
            else None
        ),
        wandb_entity=(
            args.wandb_entity
            if distributed_utils.is_master(args)
            else None
        ),
        wandb_run_name=os.environ.get(
            "WANDB_NAME", os.path.basename(args.save_dir)
        ),
        azureml_logging=False
    )
    progress.update_config(args)

    trainer.begin_epoch(epoch)

    num_updates = trainer.get_num_updates()
    logger.info("Start iterating over samples")
    for i, samples in enumerate(progress):
        with metrics.aggregate("train_inner"), torch.autograd.profiler.record_function(
            "train_step-%d" % i
        ):
            log_output = trainer.train_step(samples)
        
        if log_output is not None: # not OOM, overflow, ...
            # log mid-epoch stats
            num_updates = trainer.get_num_updates()
            if num_updates % args.log_interval == 0:
                stats = get_training_stats(metrics.get_smoothed_values("train_inner"))
                progress.log(stats, tag = "train_inner", step = num_updates)

                # reset mid-epoch stats after each log interval
                # the end-of-epoch stats will still be preserved
                metrics.reset_meters("train_inner")
            
            valid_loss = validate(
                args, trainer, epoch
            )
            
            checkpoint_utils.save_checkpoint(
                args, trainer, epoch, valid_loss
            )
            if torch.distributed.is_initialized():
                torch.distributed.barrier()

    # log end-of-epoch stats
    logger.info("end of epoch {} (average epoch stats below)".format(epoch))
    stats = get_training_stats(metrics.get_smoothed_values("train"))
    progress.print(stats, tag="train", step=num_updates)
    
    # reset epoch-level meters
    metrics.reset_meters("train")
    return valid_loss

def get_training_stats(stats: Dict[str, Any]) -> Dict[str, Any]:
    stats["wall"] = round(metrics.get_meter("default", "wall").elapsed_time, 0)
    return stats

def validate(
    args: argparse.Namespace,
    trainer: Trainer,
    epoch: int
) -> List[Optional[float]]:
    """Evaluate the model on the validation set and return the losses"""
    
    trainer.begin_valid_epoch(epoch)
    logger.info('begin validation on "{:.1f}-validation" subset'.format(args.valid_percent))

    progress = progress_bar.progress_bar(
        trainer.valid_iterator,
        log_format="json",
        log_interval=args.log_interval,
        epoch=epoch,
        tensorboard_logdir=None,
        default_log_format=("tqdm"),
        wandb_project=(
            args.wandb_project
            if distributed_utils.is_master(args)
            else None
        ),
        wandb_entity=(
            args.wandb_entity
            if distributed_utils.is_master(args)
            else None
        ),
        wandb_run_name=os.environ.get(
            "WANDB_NAME", os.path.basename(args.save_dir)
        ),
        azureml_logging=False
    )
    
    with metrics.aggregate(new_root=True) as agg:
        for i, sample in enumerate(progress):
            trainer.valid_step(sample)
    
    # log validation stats
    stats = get_valid_stats(args, trainer, agg.get_smoothed_values())
    
    if hasattr(trainer, "post_validate"):
        trainer.post_validate(
            log_output=stats,
            agg=agg,
            num_updates=trainer.get_num_updates()
        )
    
    progress.print(stats, tag="valid", step=trainer.get_num_updates())

    return stats["auroc"]

def get_valid_stats(
    args: argparse.Namespace,
    trainer: Trainer,
    stats: Dict[str, Any]
) -> Dict[str, Any]:
    stats["num_updates"] = trainer.get_num_updates()

    if not hasattr(get_valid_stats, "best"):
        get_valid_stats.best = 0

    prev_best = getattr(get_valid_stats, "best")
    best_function = max
    get_valid_stats.best = best_function(
        stats["auroc"], prev_best
    )
    
    key = "best_auroc"
    stats[key] = get_valid_stats.best

    return stats

def cli_main() -> None:
    parser = get_parser()
    args = parser.parse_args()
    distributed_utils.call_main(args, main)

if __name__ == "__main__":
    cli_main()