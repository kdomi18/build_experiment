from dotenv import load_dotenv

from build_bugs.pipeline import BugPipeline

load_dotenv()


def main():
    pipeline = BugPipeline()
    pipeline.run()


if __name__ == "__main__":
    main()
