import argparse

from .can_device.main import main as can_main
from .lin_device.main import main as lin_main


def main() -> None:
    parser = argparse.ArgumentParser(description="ISO-TP / LIN-TP demos")
    parser.add_argument("mode", choices=["can", "lin"], help="demo to run")
    args = parser.parse_args()
    if args.mode == "can":
        can_main()
    else:
        lin_main()


if __name__ == "__main__":
    main()
