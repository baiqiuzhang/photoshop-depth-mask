from ppd.utils.logger import Log
from ppd.entrys import *


def main():
    from ppd.configs.config import cfg, args
    if Log.is_main_process():
        Log.info('PPD: Pixel-Perfect Depth')
        separator = '\033[91m' + '-' * 80 + '\033[0m'
        experiment_info = f'\033[92mExperiment: \033[0m\033[94m{cfg.exp_name}\033[0m'
        print(f'{separator}\n{experiment_info}\n{separator}')
    globals()[args.entry](cfg)

if __name__ == "__main__":
    main()
