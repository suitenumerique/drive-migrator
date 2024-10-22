from core.osmose.osmose_backend import OsmoseFile


def test_extension():
    file = OsmoseFile({"originalFilename": "file.txt"})
    assert file.extension == ".txt"

    file = OsmoseFile({"downloadUrl": "http://example.com/file.txt"})
    assert file.extension == ".txt"

    file = OsmoseFile({"downloadUrl": "http://example.com/folder/file.txt"})
    assert file.extension == ".txt"

    file = OsmoseFile({"downloadUrl": "http://example.com/folder/file.bak.txt"})
    assert file.extension == ".txt"

    file = OsmoseFile({"downloadUrl": "http://example.com/fol.der/file.bak.txt"})
    assert file.extension == ".txt"

    file = OsmoseFile({"originalFilename": "file"})
    assert file.extension == ""

    file = OsmoseFile({"downloadUrl": "http://example.com/file"})
    assert file.extension == ""

    file = OsmoseFile({})
    assert file.extension == ""


def test_name_with_extension():
    file = OsmoseFile({"title": "file", "originalFilename": "file.txt"})
    assert file.name_with_extension == "file.txt"

    file = OsmoseFile({"title": "file", "downloadUrl": "http://example.com/file.txt"})
    assert file.name_with_extension == "file.txt"

    file = OsmoseFile(
        {"title": "file", "downloadUrl": "http://example.com/folder/file.txt"}
    )
    assert file.name_with_extension == "file.txt"

    file = OsmoseFile(
        {"title": "file", "downloadUrl": "http://example.com/folder/file.bak.txt"}
    )
    assert file.name_with_extension == "file.txt"

    file = OsmoseFile(
        {"title": "file", "downloadUrl": "http://example.com/fol.der/file.bak.txt"}
    )
    assert file.name_with_extension == "file.txt"

    file = OsmoseFile({"title": "file", "originalFilename": "file"})
    assert file.name_with_extension == "file"

    file = OsmoseFile({"title": "file", "downloadUrl": "http://example.com/file"})
    assert file.name_with_extension == "file"

    file = OsmoseFile({})
    assert file.name_with_extension == "None"
