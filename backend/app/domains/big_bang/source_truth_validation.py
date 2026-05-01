from __future__ import annotations

from app.source_of_truth.loader import SourceOfTruthLoader


GRAPH_LAYER_ALIASES = {
    "oasis": "oasis_interaction",
    "social": "oasis_interaction",
    "interaction": "oasis_interaction",
}


def normalize_initializer_against_source_of_truth(output: dict) -> dict:
    loader = SourceOfTruthLoader()
    emotions = _ids(loader.load_json("emotions.json").get("emotions", []))
    graph_layers = _ids(loader.load_json("graph_edge_types.json").get("layers", []))
    event_types = _ids(loader.load_json("event_types.json").get("event_types", []))
    sociology_models = _ids(loader.load_json("sociology_models.json").get("models", []))

    output["emotion_observations"] = [
        item
        for item in output.get("emotion_observations", [])
        if _key(item, "emotion", "emotion_key") in emotions
    ]
    for edge in output.get("graph_edges", []):
        layer = _key(edge, "layer", "graph_layer")
        layer = GRAPH_LAYER_ALIASES.get(layer, layer)
        edge["layer"] = layer if layer in graph_layers else "influence"
    for event in output.get("initial_events", []):
        event_type = _key(event, "event_type", "type")
        event["event_type"] = event_type if event_type in event_types else "announcement"
    for signal in output.get("sociology_baseline", []):
        model = _key(signal, "model", "sociology_model")
        signal["model"] = model if model in sociology_models else "attention_decay"
    return output


def _ids(values) -> set[str]:
    result = set()
    for item in values:
        if isinstance(item, str):
            result.add(item)
        elif isinstance(item, dict):
            result.add(str(item.get("id") or item.get("name") or item.get("key")))
    return result


def _key(item: dict, *keys: str) -> str:
    for key in keys:
        if item.get(key):
            return str(item[key])
    return ""
