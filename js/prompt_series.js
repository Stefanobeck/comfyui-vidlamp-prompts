// Vidlamp Prompt Series: when the engine is Ollama or LM Studio, ask the server which models are installed there and
// put them in the "server_model" menu (10/11/2026, the user: "if I pick Ollama it should check that Ollama is there
// and show me the list of models"). If the program does not answer, a message says what to do.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const AUTO = "auto (first installed)";
const SERVERS = ["Ollama", "LM Studio"];

function toast(severity, summary, detail) {
  const t = app.extensionManager?.toast;
  if (t?.add) t.add({ severity, summary, detail, life: 7000 });
  else console.warn(`[Vidlamp Prompt Series] ${summary}: ${detail}`);
}

async function refreshModels(node, announce) {
  const w = (name) => node.widgets?.find((x) => x.name === name);
  const engine = w("engine"), model = w("server_model"), url = w("server_url");
  if (!engine || !model) return;
  if (!SERVERS.includes(engine.value)) {
    model.options.values = [AUTO];
    model.value = AUTO;
    node.setDirtyCanvas(true, true);
    return;
  }
  const keep = model.value;
  model.options.values = ["checking " + engine.value + "..."];
  model.value = model.options.values[0];
  node.setDirtyCanvas(true, true);
  let data;
  try {
    const q = new URLSearchParams({ engine: engine.value, url: url?.value || "" });
    data = await (await api.fetchApi(`/vidlamp_prompts/models?${q}`)).json();
  } catch (e) {
    data = { models: [], error: String(e) };
  }
  if (data.error) {
    model.options.values = [AUTO];
    model.value = AUTO;
    toast("warn", `${engine.value} not found`, data.error);
  } else {
    model.options.values = [AUTO, ...data.models];
    // keep the model of a saved workflow when it is still installed
    model.value = data.models.includes(keep) ? keep : AUTO;
    if (announce) toast("success", engine.value, `${data.models.length} models found at ${data.url}`);
  }
  node.setDirtyCanvas(true, true);
}

// the system prompt of the target, shown in the node so the user can read and change it (10/11/2026, the user)
async function fillSystem(node, kind = "") {
  const target = node.widgets?.find((x) => x.name === "target");
  const box = node.widgets?.find((x) => x.name === "system_prompt");
  if (!target || !box) return;
  try {
    const q = new URLSearchParams({ target: target.value, kind });
    const data = await (await api.fetchApi(`/vidlamp_prompts/system?${q}`)).json();
    if (data.system) {
      box.value = data.system;
      node.setDirtyCanvas(true, true);
    }
  } catch (e) {
    console.warn("[Vidlamp Prompt Series] system prompt not loaded:", e);
  }
}

app.registerExtension({
  name: "vidlamp.promptSeries",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "VidlampPromptSeries") return;

    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = created?.apply(this, arguments);
      const node = this;
      for (const name of ["engine", "server_url"]) {
        const widget = this.widgets?.find((x) => x.name === name);
        if (!widget) continue;
        const cb = widget.callback;
        widget.callback = function () {
          const out = cb?.apply(this, arguments);
          refreshModels(node, true);
          return out;
        };
      }
      // a new target = its rules in the system_prompt box (a new node gets them too)
      const target = this.widgets?.find((x) => x.name === "target");
      if (target) {
        const cb = target.callback;
        target.callback = function () {
          const out = cb?.apply(this, arguments);
          fillSystem(node);
          return out;
        };
      }
      setTimeout(() => {
        const box = node.widgets?.find((x) => x.name === "system_prompt");
        if (box && !box.value) fillSystem(node);
      }, 100);
      return r;
    };

    // a saved workflow: its model is put back in the menu once the list has been read
    const configure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (info) {
      const r = configure?.apply(this, arguments);
      const model = this.widgets?.find((x) => x.name === "server_model");
      const saved = model?.value;
      if (model && saved && !model.options.values.includes(saved)) model.options.values = [AUTO, saved];
      setTimeout(() => refreshModels(this, false), 50);
      return r;
    };
  },
});

// Vidlamp Prompt Rewrite: its system_prompt box shows the rewriting rules of its target, like the Sheet Creator
app.registerExtension({
  name: "vidlamp.promptRewrite",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "VidlampPromptRewrite") return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = created?.apply(this, arguments);
      const node = this;
      const target = this.widgets?.find((x) => x.name === "target");
      if (target) {
        const cb = target.callback;
        target.callback = function () {
          const out = cb?.apply(this, arguments);
          fillSystem(node, "rewrite");
          return out;
        };
      }
      setTimeout(() => {
        const box = node.widgets?.find((x) => x.name === "system_prompt");
        if (box && !box.value) fillSystem(node, "rewrite");
      }, 100);
      return r;
    };
  },
});

// Prompt Sheet / Sheet Compare: a button that opens the folder of the results (10/11/2026, the user). The folder
// opens on the computer where ComfyUI runs; when that is another computer, a message shows the path instead.
app.registerExtension({
  name: "vidlamp.openOutputFolder",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (!["VidlampPromptSheet", "VidlampPromptSheetCompare"].includes(nodeData.name)) return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = created?.apply(this, arguments);
      const node = this;
      this.addWidget("button", "📂 Open output folder", null, async () => {
        const prefix = node.widgets?.find((w) => w.name === "filename_prefix")?.value || "";
        const last = node._vidlampLast || {};
        try {
          const res = await api.fetchApi("/vidlamp_prompts/open_folder", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ prefix, subfolder: last.subfolder || "", filename: last.filename || "" }),
          });
          const data = await res.json();
          if (!data.opened) toast("info", "Output folder", `${data.error || ""} ${data.path || ""}`.trim());
        } catch (e) {
          toast("warn", "Output folder", String(e));
        }
      }, { serialize: false });
      return r;
    };
    // remember the last sheet saved, so the folder opens with it selected
    const executed = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      const r = executed?.apply(this, arguments);
      const img = message?.images?.[0];
      if (img) this._vidlampLast = { filename: img.filename, subfolder: img.subfolder };
      return r;
    };
  },
});
