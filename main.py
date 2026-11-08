import argparse

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='NCM Studio - 本地无损音乐整理')
    parser.add_argument('--input', default='')
    parser.add_argument('--output', default='')
    args = parser.parse_args()
    from ncmstudio.gui import main
    raise SystemExit(main(args.input, args.output))
