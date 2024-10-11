from core.models import Workspace


def test_workspace_status_computed():
    """
    The status of a workspace is computed correctly based on the status of the resana and archive.
    """
    workspace = Workspace()
    assert workspace.status == Workspace.Status.NONE

    # For reference, see the table in the compute_status method.
    # resana, archive, status
    combinations = [
        (Workspace.Status.NONE, Workspace.Status.NONE, Workspace.Status.NONE),
        (Workspace.Status.NONE, Workspace.Status.PENDING, Workspace.Status.PENDING),
        (Workspace.Status.NONE, Workspace.Status.FAILURE, Workspace.Status.FAILURE),
        (Workspace.Status.NONE, Workspace.Status.SUCCESS, Workspace.Status.SUCCESS),
        (Workspace.Status.PENDING, Workspace.Status.NONE, Workspace.Status.PENDING),
        (Workspace.Status.PENDING, Workspace.Status.PENDING, Workspace.Status.PENDING),
        (Workspace.Status.PENDING, Workspace.Status.FAILURE, Workspace.Status.PENDING),
        (Workspace.Status.PENDING, Workspace.Status.SUCCESS, Workspace.Status.PENDING),
        (Workspace.Status.FAILURE, Workspace.Status.NONE, Workspace.Status.FAILURE),
        (Workspace.Status.FAILURE, Workspace.Status.PENDING, Workspace.Status.PENDING),
        (Workspace.Status.FAILURE, Workspace.Status.FAILURE, Workspace.Status.FAILURE),
        (Workspace.Status.FAILURE, Workspace.Status.SUCCESS, Workspace.Status.FAILURE),
        (Workspace.Status.SUCCESS, Workspace.Status.NONE, Workspace.Status.SUCCESS),
        (Workspace.Status.SUCCESS, Workspace.Status.PENDING, Workspace.Status.PENDING),
        (Workspace.Status.SUCCESS, Workspace.Status.FAILURE, Workspace.Status.FAILURE),
        (Workspace.Status.SUCCESS, Workspace.Status.SUCCESS, Workspace.Status.SUCCESS),
    ]

    workspace.set_status_resana(Workspace.Status.PENDING)
    assert workspace.status == Workspace.Status.PENDING
    assert workspace.status_resana == Workspace.Status.PENDING
    assert workspace.status_archive == Workspace.Status.NONE

    for resana, archive, status in combinations:
        workspace.set_status_resana(resana)
        workspace.set_status_archive(archive)
        assert workspace.status == status
        assert workspace.status_resana == resana
        assert workspace.status_archive == archive
