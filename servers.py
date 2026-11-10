"""Ollama and LM Studio: the list of models (for the menu of the node) and the text generation.

10/11/2026, the user: "when I pick Ollama it should check that Ollama is there and show me its models; add LM Studio
too". The browser asks the list to /vidlamp_prompts/models (nodes.py) every time the engine changes; the node checks it
again when it runs.
"""
import json
import urllib.error
import urllib.request

AUTO = "auto (first installed)"
DEFAULT_URL = {"Ollama": "http://127.0.0.1:11434", "LM Studio": "http://127.0.0.1:1234"}
# models that do not write free text (OCR, embeddings, rerankers, code, translation): left out of the menu
NOT_WRITERS = ("embed", "rerank", "ocr", "coder", "translate")


def url_for(engine, url=""):
    return (str(url or "").strip() or DEFAULT_URL[engine]).rstrip("/")


def _get(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(url, payload, timeout):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _writers(names):
    return [n for n in names if not any(w in n.lower() for w in NOT_WRITERS)]


def list_models(engine, url="", timeout=3):
    """(models, error): the models that write text, or an error message that says what to do."""
    base = url_for(engine, url)
    try:
        if engine == "Ollama":
            names = [m["name"] for m in _get(base + "/api/tags", timeout).get("models", [])]
            names = [n for n in names if ":cloud" not in n and "-cloud" not in n]
        else:
            data = _get(base + "/api/v1/models", timeout)
            names = [m.get("key") or m.get("id") for m in data.get("models") or data.get("data") or []
                     if m.get("type") in (None, "llm", "vlm")]
            names = [n for n in names if n]
    except (urllib.error.URLError, OSError, ValueError) as e:
        where = "the Ollama app" if engine == "Ollama" else "LM Studio and its server (Developer tab, Start Server)"
        return [], f"{engine} does not answer at {base}. Start {where}, then pick the engine again. ({e})"
    names = _writers(names)
    if not names:
        hint = "ollama pull gemma4:e4b" if engine == "Ollama" else "download a model in LM Studio"
        return [], f"{engine} has no model that writes text. Install one first ({hint})."
    return names, None


class Engine:
    """generate(system, message, seed) and unload() for Ollama or LM Studio."""

    def __init__(self, engine, model, url, temperature):
        self.engine, self.base, self.temperature = engine, url_for(engine, url), temperature
        models, error = list_models(engine, url, timeout=5)
        if error:
            raise RuntimeError(error)
        if not model or model == AUTO:
            model = models[0]
            print(f"[Vidlamp Prompt Series] {engine} model: {model} (first installed)")
        elif model not in models and f"{model}:latest" not in models:
            raise RuntimeError(f"{engine} has no model '{model}'. Installed: {', '.join(models)}. "
                               f"Pick the engine again to update the list.")
        self.model = model
        self._think = True

    def generate(self, system, message, seed):
        messages = [{"role": "system", "content": system}, {"role": "user", "content": message}]
        try:
            if self.engine == "Ollama":
                payload = {"model": self.model, "stream": False, "keep_alive": "5m", "messages": messages,
                           "options": {"temperature": self.temperature, "seed": seed}}
                if self._think:
                    payload["think"] = False
                try:
                    answer = _post(self.base + "/api/chat", payload, 600)
                except urllib.error.HTTPError as e:
                    if e.code != 400 or "think" not in payload:
                        raise
                    self._think = False             # a model without the thinking switch
                    payload.pop("think")
                    answer = _post(self.base + "/api/chat", payload, 600)
                return (answer.get("message") or {}).get("content", "")
            answer = _post(self.base + "/v1/chat/completions",
                           {"model": self.model, "messages": messages, "temperature": self.temperature,
                            "seed": seed, "stream": False}, 600)
            msg = (answer.get("choices") or [{}])[0].get("message") or {}
            text = (msg.get("content") or "").strip()
            if not text and msg.get("reasoning_content"):
                raise RuntimeError(f"LM Studio: '{self.model}' only reasoned and gave no answer. Turn its "
                                   f"reasoning off in LM Studio or pick another model.")
            return text
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if self.engine == "LM Studio" and "context" in body.lower():
                raise RuntimeError("LM Studio: the request is longer than the context the model was loaded with. "
                                   "Raise 'Context Length' for the model in LM Studio and load it again.")
            raise RuntimeError(f"{self.engine} error {e.code}: {body[:300]}")
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(f"{self.engine} stopped answering at {self.base}: {e}")

    def unload(self):
        """Frees the graphics card for ComfyUI. A courtesy: never lose the prompts for it."""
        try:
            if self.engine == "Ollama":
                _post(self.base + "/api/generate", {"model": self.model, "keep_alive": 0}, 30)
                return
            data = _get(self.base + "/api/v1/models", 10)
            for m in data.get("models") or []:
                if (m.get("key") or m.get("id")) != self.model:
                    continue
                for inst in m.get("loaded_instances") or []:
                    iid = (inst.get("id") or inst.get("instance_id")) if isinstance(inst, dict) else inst
                    if iid:
                        _post(self.base + "/api/v1/models/unload", {"instance_id": iid}, 30)
        except Exception as e:
            print(f"[Vidlamp Prompt Series] could not unload {self.model} from {self.engine}: {e}")
