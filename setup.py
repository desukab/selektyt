import os

from setuptools import setup


# The PyPI release contains a native Rust sidecar, so the production Linux
# artifact must not be advertised as a universal pure-Python wheel.
setup(
    options={
        "bdist_wheel": {
            "plat_name": os.environ.get("SELEKYT_WHEEL_PLATFORM") or None,
        }
    }
)
