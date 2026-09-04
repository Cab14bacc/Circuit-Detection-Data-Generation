"""
Configuration for the circuit data generation package.

Defines paths and defaults.

All paths are relative to circuit_data_gen, unless specified as absolute path
"""

import logging
import logging.config
import os
import importlib
import copy
from pathlib import Path
from functools import lru_cache
import sys

DATASET_DIR = str((Path(__file__).parent.parent.parent / "dataset").resolve())

LOGGING_CONFIG = {
    "LOGGING_CONFIG_PATH": "configs/logging/default_logging_config.py",
}

BUILD_SKIN_CONFIG = {
    "DEBUG_DIR": "debug/circuitikz_renders",
    "SKIN_PATH": f"{DATASET_DIR}/skins/lcapy.svg",
    "SKIN_CONFIG_PATH": "configs/skins/default_skin_config.py",
    # line width in pt for circuitikz render,
    # this means the component will have stroke-width of LINE_WIDTH * 2 pt in the svg,
    # lead path will have LINE_WIDTH, but the lead paths aren't used in the eventual circuit render
    # as wires are drawn by netlistsvg.
    "LINE_WIDTH": 0.4,
}

NETLIST_SVG_CONFIG = {
    "BIN_PATH": "../netlistsvg/bin/netlistsvg.js",
    # Same skin build_skin.py emits to; single shared skin for renders.
    "SKIN_PATH": f"{DATASET_DIR}/skins/lcapy.svg",
    # Label font
    "FONT_SIZE": 10,
    "FONT_CHAR_WIDTH": 6,
    "FONT_CHAR_HEIGHT": 11,
    # Descender allowance for baseline-anchored label boxes, as a fraction of
    # the box height. Chars like "g"/"y" paint below the baseline; a box
    # ending exactly at the baseline would clip them, so baseline boxes are
    # shifted down by HEIGHT * FONT_DESC_SHIFT. Emitted into the skin as
    # <s:properties fontDescShift="..."> by build_skin.py; netlistsvg falls
    # back to 0.3 for older skins. Only affects baseline/auto labels
    # (refs above symbols); hanging/middle labels already contain tails.
    "FONT_DESC_SHIFT": 0.3,
    # Stroke width of the wires netlistsvg draws between components.
    "WIRE_STROKE_WIDTH": 0.4 * 2,
    # annotation export settings.
    # CLASSES_PATH is the single centralized YOLO class registry, written once
    # by `cirdg build_skin` (registry is derived from the skin, so it only
    # changes when the skin changes); renders never write class files.
    "ANNOTATION": {"CLASSES_FILENAME": "classes.txt", "CLASSES_PATH": f"{DATASET_DIR}/classes.txt"},
}

CLI_CONFIG = {
    "RENDER": {"DEFAULT_RENDER_DIR": f"{DATASET_DIR}/renders_default"},
    "BUILD_SKIN": {"RENDER_PATH": "skins"},
    # base dir to projects, each project will have its own subdir
    # the default project is name "default_{%Y%m%d_%H%M%S}",
    # so the default project dir is ../dataset/projects/default_{%Y%m%d_%H%M%S}
    "GEN_NETLIST": {"DEFAULT_PROJECTS_DIR": f"{DATASET_DIR}/projects/"},
    "CONVERT": {"DEFAULT_CONVERT_DIR": f"{DATASET_DIR}/convert_default"},
}


CONVERT_CONFIG = {
    "CONVERT_CONFIG_PATH": "configs/convert/default_convert_config.py",
}


CONFIGS = {
    "build_skin": BUILD_SKIN_CONFIG,
    "netlistsvg": NETLIST_SVG_CONFIG,
    "cli": CLI_CONFIG,
    "convert": CONVERT_CONFIG,
    "logging": LOGGING_CONFIG,
}


def _path_to_module(path: str) -> str:
    """Convert a file path to a module path.

    Example
    ----------
    "configs/logging/default_logging_config.py" -> "circuit_data_gen.configs.logging.default_logging_config"
    """
    base_module_dir = Path(__file__).parent.parent.resolve()
    if not os.path.isabs(path):
        abs_path = (base_module_dir / path).resolve()
    else:
        abs_path = Path(path).resolve()

    if not abs_path.exists() or not abs_path.is_relative_to(base_module_dir):
        raise ValueError(
            f"Path '{path}' is not within the base module directory "
            f"'{base_module_dir}', can't convert to module path."
        )

    if __package__ is None:
        raise ValueError(
            "The __package__ attribute is None. This function should be called from within a package context."
        )

    # Get the relative path from the base module directory.
    rel_path = abs_path.relative_to(base_module_dir)
    module_rel = str(rel_path)
    if module_rel.endswith(".py"):
        module_rel = module_rel[:-3]
    module_rel = module_rel.replace("/", ".").replace("\\", ".")

    base_module = __package__.split(".")[0]  # Get the top-level package name (e.g., "circuit_data_gen")
    return base_module + "." + module_rel


def get_config_path_value(config_name: str, key_path: str) -> Path:
    """Get config value from the CONFIGS dictionary.

    Parameters
    ----------
    config_name : str:
        The name of the config section (e.g., "cli").
    key_path : str
        The dot-separated path to the desired key (e.g., "render.default_render_dir"). Letter case agnostic.
    """

    config_or_str = get_config_value(config_name, key_path)

    if not isinstance(config_or_str, str):
        raise ValueError(
            f"Expected a string value for config '{config_name}' and key "
            f"'{key_path}', but got type: {type(config_or_str)}"
        )

    p = Path(config_or_str)
    if not p.is_absolute():
        base_module_dir = Path(__file__).parent.parent.resolve()
        p = base_module_dir / p
    return p


def get_config_value(config_name: str, key_path: str, attr_name: str | None = None) -> str | dict:
    """Get config value from the CONFIGS dictionary.

    Parameters
    ----------
    config_name : str:
        The name of the config section (e.g., "build_skin").
    key_path : str
        The dot-separated path to the desired key (e.g., "DEBUG_DIR"). Letter case agnostic.
    attr_name : str, optional
        If specified, will attempt to retrieve this attribute from a .py file.
    """
    # "a.b.c" in TOML is config["a"]["b"]["c"]
    cur_config_or_value = CONFIGS.get(config_name)

    if cur_config_or_value is None:
        raise ValueError(f"Config '{config_name}' not found in CONFIGS.")

    for part in key_path.split("."):
        cur_config_or_value = cur_config_or_value.get(
            part.lower(), cur_config_or_value.get(part.upper(), None)
        )
        if cur_config_or_value is None:
            raise ValueError(f"Key path '{key_path}' not found in config '{config_name}'.")

    if attr_name is not None:
        if isinstance(cur_config_or_value, str) and cur_config_or_value.endswith(".py"):
            import importlib  # noqa: PLC0415

            module_path = _path_to_module(cur_config_or_value)
            module = importlib.import_module(module_path)
            cur_config_or_value = getattr(module, attr_name, None)
            if cur_config_or_value is None:
                raise ValueError(f"Attribute '{attr_name}' not found in module '{module_path}'")
        else:
            raise ValueError(
                f"When attr_name is specified, expected a .py file for config '{config_name}' "
                f"and key '{key_path}', but got: {cur_config_or_value}"
            )

    return cur_config_or_value


_logging_initialized = False


def _init_logging(extra_log_path: str | None = None):
    """initalizes logging

    Parameters
    ----------
    extra_log_path : str, optional
        Extra log file if user wants for example to log the current run in a separate file.
        Absolute or relative to the package root (circuit_data_gen/).
    """
    global _logging_initialized
    if _logging_initialized:
        return

    logging_config_path = get_config_value("logging", "logging_config_path")
    if logging_config_path is None or not isinstance(logging_config_path, str):
        raise ValueError("Missing 'logging_config_path' in config.py")

    # Resolve module file relative path against the package root (circuit_data_gen/).
    package_root = Path(__file__).parent.parent.resolve()
    if not os.path.isabs(logging_config_path):
        candidate = package_root / logging_config_path
        if candidate.exists():
            logging_config_path = str(candidate)

    if not os.path.exists(logging_config_path):
        raise FileNotFoundError(f"Logging config file not found: {logging_config_path}")

    module_path = _path_to_module(logging_config_path)
    module = importlib.import_module(module_path)

    config_dict = copy.deepcopy(getattr(module, "LOGGING_CONFIG", None))
    if config_dict is None:
        raise ValueError(f"LOGGING_CONFIG not found in module '{module_path}'")

    # If extra_log_path is provided, add a file handler to the config.
    if extra_log_path is not None:
        if not os.path.isabs(extra_log_path):
            extra_log_path = str((package_root / extra_log_path).resolve())
        file_handler_config = {
            "class": "logging.FileHandler",
            "filename": extra_log_path,
            "formatter": "standard",
            "level": "DEBUG",
        }
        config_dict["handlers"]["extra_file"] = file_handler_config
        config_dict["root"]["handlers"].append("extra_file")

    # Resolve any relative paths in the logging config against the package root.
    for handler_cfg in config_dict.get("handlers", {}).values():
        if "filename" in handler_cfg:
            handler_cfg["filename"] = str((package_root / handler_cfg["filename"]).resolve())

    sys.stdout.reconfigure(encoding=sys.stdout.encoding, errors="replace")
    logging.config.dictConfig(config_dict)
    logging.getLogger("__init_smoke__").info("logging initialized")
    _logging_initialized = True


# given the same arguments, the same logger is returned.
@lru_cache(maxsize=None)
def get_logger(
    dunder_name: str,
    extra_log_path: str | None = None,
) -> logging.Logger:
    """returns a logger for the given module

    Parameters
    ----------
    dunder_name : str
        The name of the logger (typically the __name__ of the module).
    extra_log_path : str, optional
        Extra log file if user wants for example to log the current run in a separate file.
        This is different from passing a log file path in the logging config, as it allows
        a extra log file only for this logger.
    """
    _init_logging()
    logger = logging.getLogger(dunder_name)

    # If log_path is provided, add the file handler.
    if extra_log_path is not None:
        file_handler = logging.FileHandler(extra_log_path)
        # we would need to set logger level as well, but it will affect upstream loggers
        # TODO: figure it out, maybe use filters
        # file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)

    return logger
