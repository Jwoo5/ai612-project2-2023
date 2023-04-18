import argparse

def get_parser():
    """
    Note:
        Do not add command-line arguments here when you submit the codes.
        Keep in mind that we will run your pre-processing code by this command:
        `python 00000000_preprocess.py ./train --dest ./output`
        which means that we might not be able to control the additional arguments.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "root",
        metavar="DIR",
        help="root directory containing different ehr files to pre-process (usually, 'train/')"
    )
    parser.add_argument(
        "--dest",
        type=str,
        metavar="DIR",
        help="output directory"
    )

    parser.add_argument(
        "--sample_filtering",
        type=bool,
        default=True,
        help="indicator to prevent filtering from being applies to the test dataset."
    )
    return parser

def main(args):
    """
    TODO:
        Implement your feature preprocessing function here.
        Rename the file name with your student number.
    
    Note:
        1. This script should dump processed features to the --dest directory.
        Note that --dest directory will be an input to your dataset class (i.e., --data_path).
        You can dump any type of files such as json, cPickle, or whatever your dataset can handle.

        2. If you use vocabulary, you should specify your vocabulary file(.pkl) in this code section.
        Also, you must submit your vocabulary file({student_id}_vocab.pkl) along with the scripts.
        Example:
            with open('./20231234_vocab.pkl', 'rb') as f:
                (...)

        3. For fair comparison, we do not allow to filter specific samples when using test dataset.
        Therefore, if you filter some samples from the train dataset,
        you must use the '--sample_filtering' argument to prevent filtering from being applied to the test dataset.
        We will set the '--sample_filtering' argument to False and run the code for inference.
        We also check the total number of test dataset.
    """

    root_dir = args.root
    dest_dir = args.dest

if __name__ == "__main__":
    parser = get_parser()
    args = parser.parse_args()
    main(args)