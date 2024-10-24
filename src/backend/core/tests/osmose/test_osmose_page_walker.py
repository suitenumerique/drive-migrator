from unittest.mock import Mock

import pytest

from core.osmose.osmose_real_backend import PageWalker

PAGE_SIZE = 100
MAX = 500


@pytest.fixture
def mock_callback():
    return Mock()


def test_empty(mock_callback):
    # Mock callback for a single page
    mock_callback.return_value = {
        "total": 0,
        "start": 0,
        "sort": "asc",
        "dataSet": [],
    }

    walker = PageWalker(mock_callback)
    result = walker.walk()

    # Check if the result has all the items
    assert len(result) == 0
    assert result == []

    # Check if the callback was called only once
    mock_callback.assert_called_once_with(pageSize=PAGE_SIZE, start=0)


def test_single_page(mock_callback):
    # Mock callback for a single page
    mock_callback.return_value = {
        "total": 100,
        "start": 0,
        "sort": "asc",
        "dataSet": list(range(100)),  # Simulating 100 items
    }

    walker = PageWalker(mock_callback)
    result = walker.walk()

    # Check if the result has all the items
    assert len(result) == 100
    assert result == list(range(100))

    # Check if the callback was called only once
    mock_callback.assert_called_once_with(pageSize=PAGE_SIZE, start=0)


def test_multiple_pages(mock_callback):
    # Mock callback for multiple pages
    mock_callback.side_effect = [
        {
            "total": 250,
            "start": 0,
            "sort": "asc",
            "dataSet": list(range(100)),  # Simulating first 100 items
        },
        {
            "total": 250,
            "start": 100,
            "sort": "asc",
            "dataSet": list(range(100, 200)),  # Next 100 items
        },
        {
            "total": 250,
            "start": 200,
            "sort": "asc",
            "dataSet": list(range(200, 250)),  # Final 50 items
        },
    ]

    walker = PageWalker(mock_callback)
    result = walker.walk()

    # Check if the result has all the items
    assert len(result) == 250
    assert result == list(range(250))

    # Check if the callback was called 3 times
    assert mock_callback.call_count == 3


def test_no_data(mock_callback):
    # Mock callback with no data
    mock_callback.return_value = {"total": 0, "start": 0, "sort": "asc", "dataSet": []}

    walker = PageWalker(mock_callback)
    result = walker.walk()

    # Result should be an empty list
    assert result == []

    # Check if the callback was called once
    mock_callback.assert_called_once_with(pageSize=PAGE_SIZE, start=0)


def test_max_limit_reached(mock_callback):
    # Mock callback to always return data so that the walker reaches the max count
    mock_callback.return_value = {
        "total": 1000000,
        "start": 0,
        "sort": "asc",
        "dataSet": list(range(100)),  # Simulating 100 items in each call
    }

    walker = PageWalker(mock_callback)
    result = walker.walk()

    # Ensure that we break after MAX requests
    assert len(result) == MAX * PAGE_SIZE  # Should retrieve only MAX * PAGE_SIZE items

    # Check if the callback was called exactly MAX times
    assert mock_callback.call_count == MAX


def test_partial_final_page(mock_callback):
    # Mock callback for partial final page
    mock_callback.side_effect = [
        {
            "total": 210,
            "start": 0,
            "sort": "asc",
            "dataSet": list(range(100)),  # First 100 items
        },
        {
            "total": 210,
            "start": 100,
            "sort": "asc",
            "dataSet": list(range(100, 200)),  # Next 100 items
        },
        {
            "total": 210,
            "start": 200,
            "sort": "asc",
            "dataSet": list(range(200, 210)),  # Final 10 items
        },
    ]

    walker = PageWalker(mock_callback)
    result = walker.walk()

    # Check if the result has all the items
    assert len(result) == 210
    assert result == list(range(210))

    # Check if the callback was called 3 times
    assert mock_callback.call_count == 3
