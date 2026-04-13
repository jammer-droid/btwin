"""Public core package boundary for the future CLI-first split."""

__all__ = ["BTwin", "BTwinConfig"]


def __getattr__(name: str):
    if name == "BTwin":
        from btwin_core.btwin import BTwin

        return BTwin
    if name == "BTwinConfig":
        from btwin_core.config import BTwinConfig

        return BTwinConfig
    raise AttributeError(name)
