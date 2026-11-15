from flask import request


def clean_args() -> dict[str, str]:
    return {k: v for k, v in request.args.items() if v != ""}