"""Explicit scene selection for deterministic CLI replay of existing journals."""

from .sensory_play import ToyScene


SENSORY_SCENE_CHOICES = ("default", "empty")


def resolve_sensory_scene(selection, *, sensory_mode, room):
    """Preserve legacy defaults or construct an explicitly requested empty scene.

    Call after room admission. Never infer a scene from journal contents or
    resize the built-in toys: the existing bootstrap check must match it.
    """
    if selection == "default":
        return None
    if selection != "empty":
        raise ValueError("unsupported sensory scene selection")
    if sensory_mode not in ("toy_probe", "toy_probe_v2"):
        raise ValueError("--sensory-scene empty requires toy_probe or toy_probe_v2")
    if room is None:
        return ToyScene(toys=())
    return ToyScene(width=room.width, height=room.height, toys=())
