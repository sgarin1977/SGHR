import ast
from pathlib import Path


CONFIG_PATH = Path("config.py")
BOT_PATH = Path("bot.py")


def test_pending_updates_are_kept_by_default():
    source = CONFIG_PATH.read_text(
        encoding="utf-8-sig"
    )
    compact = "".join(source.split())

    assert "BOT_DROP_PENDING_UPDATES" in source
    assert "_get_bool_env(" in source
    assert (
        '"BOT_DROP_PENDING_UPDATES",'
        "default=False"
        in compact
    )


def test_bot_uses_configured_update_policy():
    source = BOT_PATH.read_text(
        encoding="utf-8-sig"
    )
    tree = ast.parse(source)

    delete_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(
            node.func,
            ast.Attribute,
        )
        and node.func.attr == "delete_webhook"
    ]

    assert len(delete_calls) == 1

    call = delete_calls[0]
    keyword = next(
        item
        for item in call.keywords
        if item.arg
        == "drop_pending_updates"
    )

    assert isinstance(
        keyword.value,
        ast.Name,
    )
    assert (
        keyword.value.id
        == "BOT_DROP_PENDING_UPDATES"
    )

    assert (
        "BOT_DROP_PENDING_UPDATES"
        in source
    )
    assert (
        "drop_pending_updates=True"
        not in source
    )


def test_pending_update_policy_is_documented():
    source = Path(".env.example").read_text(
        encoding="utf-8-sig"
    )

    assert (
        "BOT_DROP_PENDING_UPDATES=false"
        in source
    )
