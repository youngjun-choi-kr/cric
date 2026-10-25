"""Step 0: confirm the Token Factory key works and which Nemotron models this account can call."""

from common import MODEL_NANO, MODEL_SUPER, MODEL_VL, token_factory_client


def main() -> None:
    client = token_factory_client()
    ids = sorted(m.id for m in client.models.list().data)
    nemotron = [i for i in ids if "nemotron" in i.lower()]
    print(f"{len(ids)} models available, {len(nemotron)} Nemotron:")
    for i in nemotron:
        print("  ", i)

    print("\nConfigured models:")
    for label, model in [("SUPER", MODEL_SUPER), ("VL", MODEL_VL), ("NANO", MODEL_NANO)]:
        status = "OK" if model in ids else "NOT FOUND -> set MODEL_%s in .env" % label
        print(f"  {label:5} {model}  [{status}]")


if __name__ == "__main__":
    main()
