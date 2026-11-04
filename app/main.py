from app.agent import run_agent


def main() -> None:
    prompt = input("What would you like to check? ")
    print(run_agent(prompt))


if __name__ == "__main__":
    main()
