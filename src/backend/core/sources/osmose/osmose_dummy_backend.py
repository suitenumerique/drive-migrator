from core.sources.osmose.osmose_backend import OsmoseBackend, OsmoseWorkspace


class OsmoseDummyBackend(OsmoseBackend):
    def get_workspaces(self, user):
        workspaces = [
            OsmoseWorkspace("aaa", "Éco-responsabilité Pays de la Loire"),
            OsmoseWorkspace("bbb", "Préparation budget 2025"),
        ]
        return workspaces
