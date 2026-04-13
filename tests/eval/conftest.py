"""
tests/eval/conftest.py

Pytest configuration for the eval suite.
Adds --runs option for multi-run averaging.
"""


def pytest_addoption(parser):
    parser.addoption(
        "--runs",
        action="store",
        default="1",
        type=int,
        help="Number of times to run each golden case (default: 1, recommended: 3)",
    )
