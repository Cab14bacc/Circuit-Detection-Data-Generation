import tomllib
from pathlib import Path


def get_config_path_value(section: str, key: str) -> Path:
    config_path = Path(__file__).parent / "configs" / "config.toml"
    with open(config_path, 'rb') as f:
        config = tomllib.load(f)

    # "a.b.c" in TOML is config["a"]["b"]["c"]
    section_config = config
    for part in section.split("."):
        section_config = section_config.get(part)
        if section_config is None:
            raise ValueError(f"Missing '{section}' section in config.toml")

    key_path = section_config.get(key)
    if key_path is None:
        raise ValueError(f"Missing '{key}' in '{section}' section of config.toml")

    return Path(__file__).parent / key_path