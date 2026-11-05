import pytest

from stardew_planner.data_loader import load_game_data


@pytest.fixture(scope="session")
def data():
    return load_game_data()


@pytest.fixture(scope="session")
def crops(data):
    return {c.name: c for c in data.crops}
