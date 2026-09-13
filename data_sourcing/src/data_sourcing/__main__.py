import uvicorn


def main() -> None:
    uvicorn.run(
        "data_sourcing.api:create_app",
        host="127.0.0.1",
        port=8001,
        reload=False,
        factory=True,
    )


if __name__ == "__main__":
    main()
