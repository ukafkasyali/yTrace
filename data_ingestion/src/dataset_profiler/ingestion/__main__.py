import uvicorn

from .api import create_app


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=8002)


if __name__ == "__main__":
    main()
