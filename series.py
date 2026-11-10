"""The logic of Vidlamp Prompt Series, without ComfyUI imports (so it can be tried on its own).

A series is N prompts for one target model, written from a short idea or from the ready-made presets:
- engine "Presets (no AI)": the prompts are put together from the ready prompts, the styles and the random character
  pieces in data/. Instant, no text model.
- engine "ComfyUI text model": a text encoder that ComfyUI can make speak (Gemma, Qwen3-VL...) writes each prompt,
  following the writing guide of the target model.
- engine "Ollama": the same, with a model served by Ollama.

The randomness is decided here, from the seed (a 12B text model repeats the same ideas): setting, time, mood and twist
are drawn from lists, the text model turns them into one coherent scene in the format of the target model.
"""
import json
import random
import re
from pathlib import Path

from . import characters

DATA = Path(__file__).resolve().parent / "data"

ENGINES = ["Presets (no AI)", "ComfyUI text model", "Ollama", "LM Studio"]
MODES = ["Explore (different scenes)", "Variations (same scene, new details)"]
FRAMES = ["Landscape 16:9", "Portrait 9:16", "Square 1:1"]
FRAME_SETTINGS = {"Landscape 16:9": "Width 1280, Height 720 (landscape)", "Portrait 9:16": "Width 720, Height 1280 (portrait)",
                  "Square 1:1": "Width 1024, Height 1024 (square)"}
CATEGORY_ANY = "Any"

RANDOM_AI = """You are the random idea generator of an image and video app. Invent ONE original scene from the RANDOM BRIEF and write it as the final prompt, following the format rules of the target model given after this text.

Rules:
1. Use everything in the RANDOM BRIEF: the category, the style and the ingredients (setting, time, mood, twist). Combine them into one coherent, concrete scene that makes sense.
2. Surprising but easy to picture: one clear main subject (a person, an animal, an object or a place), what it is doing, where, and the light. Give specific details: materials, colors, clothes, weather, the age and origin of people. No vague words.
3. Vary the people: age, gender, origin, body. Do not default to a young woman.
4. Never reuse a subject or a setting listed in RECENTLY MADE.
5. The STYLE line decides the medium (photograph, watercolor, pixel art...): write it the way the format below asks.
6. When the brief has a subject and a framing (category Characters), the image is that one character with that framing: invent a specific look, clothes, expression and setting.
7. People are always adults (20 or older) and invented: never a real or famous person.
8. Safe for work only.
9. When the brief has a USER IDEA, it is the main subject and it wins over everything else: keep exactly what the user asked (who or what, and every detail they gave, in any language) and invent only what the idea leaves open, taking it from the brief. If the idea already says where, when or how, keep it and use the brief only for what is missing. Rule 4 does not apply to the subject of the USER IDEA: it stays, only the rest changes.
10. When the brief says VARIATION, keep the subject, the place and the style of the FIRST VERSION and change only the details it names.
Output only the prompt."""

COMMON_RULES = """General rules:
- Read the IDEA in any language. Write in English unless the format above says otherwise; keep any text that must be visible, and any spoken words, exactly as given, in their original language, inside double quotes.
- When a detail is missing pick one concrete, plausible choice instead of vague or hedging words.
- SETTINGS give the frame: compose the scene for it, but never write the frame, its words or its numbers into the output.
- If LORA TRIGGER WORDS are given, include each one verbatim at the start.
- The context lines (TASK, SETTINGS, CURRENT PROMPT, RANDOM BRIEF, STYLE, VARIATION, FIRST VERSION, RECENTLY MADE, LORA TRIGGER WORDS) are information for you: never copy them, their labels or their numbers into the output.
- Lines of the format rules about inputs this request does not have (pictures, audio, timed lines, shot changes) do not apply: ignore them.
- In any nude or sexual context every person must be an adult, and you say so; never minors.
- Output only the final text: no title, no explanation, no markdown, no quotes around it."""

SETTINGS = [
    "a busy night market", "an abandoned greenhouse", "a lighthouse kitchen", "a wooden mountain hut", "a rooftop garden",
    "an underwater city", "a desert gas station", "a snowy train station", "a flooded library", "a circus backstage",
    "a narrow canal with boats", "a space elevator lobby", "a bamboo forest", "a 1950s diner", "a black sand beach",
    "a botanical laboratory", "a medieval bakery", "a cliffside monastery", "a neon arcade", "a sunflower field",
    "a tiny bookshop", "a frozen lake", "a jazz club", "a fishing village", "an old observatory", "a theater stage",
    "a rainy bus stop", "a hot air balloon basket", "a secret garden", "a vintage barber shop", "a jungle river",
    "a windmill on a hill", "a busy kitchen of a restaurant", "a museum at night", "an orbiting space station",
]
TIMES = ["dawn", "golden hour", "midday sun", "blue hour", "midnight", "a stormy afternoon", "a foggy morning",
         "a snowy evening", "a rainy night", "a hot summer noon"]
MOODS = ["playful", "mysterious", "cozy", "epic", "melancholic", "funny", "serene", "tense", "dreamy", "nostalgic",
         "joyful", "eerie"]
TWISTS = [
    "an unexpected animal", "everything is miniature", "one giant object", "reflections everywhere", "a strong wind",
    "objects floating in the air", "an unusual job", "two generations together", "a small celebration",
    "falling petals", "a hidden door", "glowing plants", "an old machine", "one surprising color", "a crowd watching",
    "a lost item", "a mirror", "a shadow that does not match", "a delivery arriving", "a sudden splash of water",
]
STRANGE = ("strange mix: put the subject in an absurd, surreal but beautiful combination of place, weather, light and "
           "action that do not normally go together")
VARIATION_CHANGES = ["the pose and the gesture", "the facial expression", "the camera angle and the framing",
                     "the light and the time of day", "the clothes and the colors", "the weather",
                     "a small action happening in the background", "the lens and the distance from the subject"]

# for the "Presets (no AI)" engine with video targets: the photo prompt becomes a shot, with motion and sound
VIDEO_MOTION = [
    "The camera pushes in slowly toward the subject.",
    "The camera tracks slowly to the side, revealing more of the place.",
    "A slow handheld drift keeps the subject in the center of the frame.",
    "The camera stays still while the light and small natural movements change the frame.",
    "The camera tilts up slowly from the ground to the subject.",
]
VIDEO_SOUND = [
    "Soft room tone, no music.",
    "Wind and distant ambient sounds, no music.",
    "The natural sounds of the place, close and detailed, no music.",
    "A low, quiet ambience of the place, no music.",
]

# style application without a text model (same rules as Vidlamp's ready prompts)
PHOTO_TECHNIQUE = re.compile(
    r",?\s*(?:\b\d+\s?mm(?:\s+(?:telephoto|macro|fisheye))?\s+lens(?:\s+(?:wide open|at (?:a )?wide aperture|with fast "
    r"shutter))?|\b(?:shallow|deep|very shallow) depth of field|\bcreamy bokeh|\bbokeh|\b(?:fine |soft |visible )?film "
    r"grain|\b(?:fine|soft|visible) grain|\blong exposure|\bfast shutter(?: freezing [^,.]*)?)", re.I)
OPENING = re.compile(r"^(?:An?|The)\s+[^.:]{0,90}?\b(?:photograph|photo|portrait|shot|scene|illustration|image|"
                     r"picture|close-up|seascape|landscape|cityscape|interior|still|render|painting|poster)\b[^.:]{0,40}?"
                     r"\s+(?:of|depicting)\s+", re.I)
SDXL_PHOTO = re.compile(r"\bphoto\b|\blens\b|depth of field|bokeh|grain|telephoto|macro|exposure|\d+mm", re.I)


def _json(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def guides():
    return _json("guides.json")


def targets():
    return list(guides().keys())


def _styles():
    return _json("styles.json")["stili"]


def style_names():
    return ["None", "Random"] + [s["nome"]["en"] for s in _styles()]


def _categories(family):
    return _json(f"{family}.json")["categorie"]


def category_names():
    """One list for every family (the ComfyUI menu is fixed): Any, then the English names in the order they appear."""
    names = [CATEGORY_ANY]
    for family in ("photo", "sdxl", "anime"):
        for c in _categories(family):
            label = c["label"]["en"]
            if label not in names:
                names.append(label)
    return names


def _clean(text):
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r",\s*\.", ".", text)
    # "lamps., the whole figure" (a removed lens before a comma): one sentence ends, the next starts with a capital
    text = re.sub(r"\.\s*,\s*(\w)", lambda m: ". " + m.group(1).upper(), text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def apply_style(text, style, family):
    text = str(text or "").strip()
    if not text or not style:
        return text
    photographic = bool(style.get("fotografico"))
    if family == "photo":
        if not photographic:
            text = _clean(PHOTO_TECHNIQUE.sub("", text))
        opening = style.get("apertura")
        if opening:
            if OPENING.match(text):
                text = OPENING.sub(opening + " ", text, count=1)
            else:
                text = f"{re.sub(r'\s+(?:of|depicting)$', '', opening)}. {text}"
        traits = style.get("tratti")
        if traits:
            text = f"{text.rstrip(' .')}. {traits[0].upper()}{traits[1:]}."
        return _clean(text)
    if family == "sdxl":
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if not photographic:
            parts = [p for p in parts if not SDXL_PHOTO.search(p)]
        return _clean(", ".join(parts + [style.get("tag", "")]))
    if family == "anime":
        tag = style.get("tag_anime") or style.get("tag", "")
        m = re.search(r"\.\s+(?=[A-Z])", text)
        if m:
            return _clean(f"{text[:m.start()]}, {tag}{text[m.start():]}")
        return _clean(f"{text.rstrip(' .')}, {tag}")
    return text


def style_line(style):
    if not style:
        return ""
    return (f"STYLE (chosen by the user: the whole image is in this style, it replaces any medium or look in the IDEA; "
            f"write its medium and visible traits the way the format above asks): {style['nome']['en']}: "
            f"{style.get('descrizione') or ''}").rstrip(": ")


def clean_output(text):
    """Removes what text models add around the prompt, without touching the prompt."""
    t = re.sub(r"<think>.*?</think>", "", str(text or ""), flags=re.S).strip()
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    t = re.sub(r"^\**(final prompt|prompt|output)\**\s*:\s*\**\s*", "", t, flags=re.I).strip()
    for o, c in (('"', '"'), ("'", "'"), ("“", "”")):
        count = t.count(o) if o == c else t.count(o) + t.count(c)
        if len(t) >= 2 and t.startswith(o) and t.endswith(c) and count == 2:
            t = t[1:-1].strip()
    return t


class Plan:
    """What the user chose, checked, with clear errors."""

    def __init__(self, idea, target, category, style, count, mode, strange_mix, seed, trigger_words="",
                 frame="Landscape 16:9"):
        g = guides()
        if target not in g:
            raise ValueError(f"Unknown target model '{target}'. Pick one from the list.")
        self.target = target
        self.guide = g[target]["guide"]
        self.family = g[target]["family"]
        self.video = bool(g[target]["video"])
        self.idea = " ".join(str(idea or "").split())
        self.count = max(1, int(count))
        self.variations = str(mode).startswith("Variations")
        self.strange = bool(strange_mix)
        self.rng = random.Random(int(seed))
        self.trigger_words = str(trigger_words or "").strip()
        self.settings = FRAME_SETTINGS.get(frame, FRAME_SETTINGS["Landscape 16:9"])
        cats = _categories(self.family)
        if category in (None, "", CATEGORY_ANY):
            self.category = None
        else:
            self.category = next((c for c in cats if c["label"]["en"] == category), None)
            if self.category is None and not self.idea:
                valid = ", ".join(c["label"]["en"] for c in cats)
                raise ValueError(f"The category '{category}' has no presets for {target}. "
                                 f"Valid categories for this model: {CATEGORY_ANY}, {valid}.")
        self._cats = cats
        self._styles = [s for s in _styles() if self.family in (s.get("famiglie") or [self.family])]
        self._random_style = style == "Random"
        if style in (None, "", "None", "Random"):
            self.style = None
        else:
            self.style = next((s for s in _styles() if s["nome"]["en"] == style), None)
            if self.style is None:
                raise ValueError(f"Unknown style '{style}'.")
            if self.style not in self._styles:
                valid = ", ".join(s["nome"]["en"] for s in self._styles)
                raise ValueError(f"The style '{style}' does not fit {target}. Styles for this model: {valid}.")

    def pick_style(self):
        if self._random_style:
            return self.rng.choice(self._styles) if self._styles else None
        return self.style

    def pick_category(self):
        if self.category is not None:
            return self.category
        return self.rng.choice(self._cats)

    def ingredients(self):
        return {"setting": self.rng.choice(SETTINGS), "time": self.rng.choice(TIMES),
                "mood": self.rng.choice(MOODS), "twist": STRANGE if self.strange else self.rng.choice(TWISTS)}

    def with_triggers(self, text):
        if self.trigger_words and not text.lower().startswith(self.trigger_words.lower()):
            return f"{self.trigger_words}, {text}"
        return text


# ------------------------------------------------------------------ engine: presets (no AI)
def _as_video(text):
    if OPENING.match(text):
        return OPENING.sub("A cinematic shot of ", text, count=1)
    return f"A cinematic shot. {text}"


def _when(time_of_day):
    """'at dawn', 'on a rainy night', 'in the midday sun'."""
    if time_of_day.startswith("a "):
        return f"on {time_of_day}"
    if time_of_day == "midday sun":
        return "in the midday sun"
    return f"at {time_of_day}"


def _a(word):
    return "an" if word[:1].lower() in "aeiou" else "a"


def _from_idea(plan, ing):
    """Without a text model the twist is left out (in a test, 'two generations together' replaced the cat chef with
    three old men): only place, time and mood around the idea, plus the surreal touch when strange_mix is on."""
    idea = plan.idea.rstrip(" .")
    mood = ing["mood"]
    if plan.family == "sdxl":
        parts = [idea, ing["setting"], ing["time"], f"{mood} mood"] + (["surreal"] if plan.strange else [])
        return ", ".join(parts)
    if plan.family == "anime":
        return f"{idea}, {mood}, scenery. In {ing['setting']} {_when(ing['time'])}."
    strange = ", in an absurd, surreal but beautiful combination" if plan.strange else ""
    return (f"{idea[:1].upper()}{idea[1:]}, in {ing['setting']} {_when(ing['time'])}, with {_a(mood)} {mood} "
            f"mood{strange}.")


def _new_light(plan, text, time_of_day):
    """Presets variation of a ready prompt: the same scene at another time of day (no text model to rewrite it)."""
    if plan.family == "photo":
        return f"{text.rstrip(' .')}. The scene is seen at {time_of_day}, with the light that goes with it."
    if plan.family == "anime":
        # tags first, then the sentence: the time goes at the end of the tags
        m = re.search(r"\.\s+(?=[A-Z])", text)
        if m:
            return f"{text[:m.start()]}, {time_of_day}{text[m.start():]}"
    return f"{text.rstrip(' .,')}, {time_of_day}"


def presets_series(plan, log=print):
    out, used = [], set()
    first_ing = first_text = None
    times = plan.rng.sample(TIMES, len(TIMES))
    for i in range(plan.count):
        style = plan.pick_style()
        again = plan.variations and i > 0
        if plan.idea:
            ing = {**first_ing, "time": times[i % len(times)]} if again else plan.ingredients()
            first_ing = first_ing or ing
            text = _from_idea(plan, ing)
        elif again:
            # variations: the first scene again, at another time of day (and in another style when Random)
            text = _new_light(plan, first_text, times[i % len(times)])
        else:
            cat = plan.pick_category()
            if cat.get("componi") == "characters":
                text = characters.componi(plan.family, rng=plan.rng, strano=plan.strange)["testo"]
            else:
                pool = [p for p in cat["prompt"] if p["testo"] not in used] or cat["prompt"]
                pick = plan.rng.choice(pool)
                used.add(pick["testo"])
                text = pick["testo"]
            first_text = first_text or text
        text = apply_style(text, style, plan.family)
        if plan.video:
            text = f"{_as_video(text)} {plan.rng.choice(VIDEO_MOTION)} {plan.rng.choice(VIDEO_SOUND)}"
        out.append(plan.with_triggers(text))
    if plan.variations and not plan.idea:
        log("Presets (no AI): the variations change the time of day; with a text model they change pose, angle "
            "and details too.")
    return out


# ------------------------------------------------------------------ engines with a text model
def system_for(target):
    """The whole system prompt the text model receives for a target: the idea generator, the writing guide of the target
    model and the common rules. Shown in the node (system_prompt field) so the user can read and change it."""
    g = guides()[target]
    kind = "video" if g["video"] else "image"
    return (f"{RANDOM_AI}\n\nTARGET: {target} ({kind}).\n\nFORMAT RULES OF THE TARGET MODEL:\n{g['guide']}\n\n"
            f"{COMMON_RULES}")


def system_prompt(plan):
    return system_for(plan.target)


def briefs(plan):
    """The N briefs, one per prompt (decided here from the seed, so the series can be repeated)."""
    out = []
    for i in range(plan.count):
        style = plan.pick_style()
        if plan.variations and i > 0:
            brief = {k: v for k, v in out[0]["brief"].items()}
            change = plan.rng.sample(VARIATION_CHANGES, 2)
            brief["VARIATION"] = f"version {i + 1}: change only {change[0]} and {change[1]}"
            style = out[0]["style"]
        else:
            if plan.idea:
                brief = {"USER IDEA": plan.idea}
            else:
                cat = plan.pick_category()
                brief = {"category": cat["label"]["en"]}
                if cat.get("componi") == "characters":
                    brief.update(characters.brief(rng=plan.rng))
            ing = plan.ingredients()
            if "subject" in brief:
                ing.pop("setting")
            brief.update(ing)
        out.append({"brief": brief, "style": style})
    return out


def render_message(plan, item, recent, first_text):
    lines = ["TASK: CREATE", f"SETTINGS: {plan.settings}", "RANDOM BRIEF: " + "; ".join(f"{k}: {v}" for k, v in item["brief"].items()
                                                         if k != "VARIATION")]
    if item["style"]:
        lines.append(style_line(item["style"]))
    if plan.trigger_words:
        lines.append(f"LORA TRIGGER WORDS: {plan.trigger_words}")
    if "VARIATION" in item["brief"] and first_text:
        lines.append(f"FIRST VERSION: {first_text}")
        lines.append(f"VARIATION: {item['brief']['VARIATION']}")
    elif recent:
        lines.append("RECENTLY MADE (do something different): " + " | ".join(recent))
    return "\n".join(lines)


def ai_series(plan, generate, log=print, system=None):
    """generate(system, message, seed) -> text. One call per prompt. system: the user's own system prompt (the
    system_prompt field of the node), or None for the one of the target."""
    system = (system or "").strip() or system_prompt(plan)
    out, recent = [], []
    for i, item in enumerate(briefs(plan)):
        message = render_message(plan, item, recent, out[0] if out else "")
        seed = plan.rng.randint(0, 2 ** 31 - 1)
        text = clean_output(generate(system, message, seed))
        if not text:
            raise RuntimeError(f"The text model gave an empty answer for prompt {i + 1}.")
        text = plan.with_triggers(text)
        log(f"[Vidlamp Prompt Series] {i + 1}/{plan.count}: {text[:90]}...")
        out.append(text)
        recent.append(" ".join(text.split()[:14]))
        del recent[:-10]
    return out


# ------------------------------------------------------------------ one prompt for each model (Prompt Rewrite node)
# 10/11/2026, the user: in the model comparison, besides "the same prompt for every model", also "each model gets the
# prompt written for it". The Sheet Creator writes the scenes once; each model's Rewrite node rewrites them with the
# rules of that model, keeping the same scene, so the comparison is "every model at its best" on the same ideas.
REWRITE = """You rewrite an image prompt for another model. The CURRENT PROMPT describes a scene: rewrite it as the final prompt for the target model, following its format rules given after this text.

Rules:
1. Keep the scene exactly: the same subject, people, action, place, time, light, mood, style and every concrete detail (colors, clothes, materials, objects, visible text). Add nothing new and drop nothing that matters.
2. Change only the form: order, length, wording and technical terms as the format rules of the target model ask.
3. Keep any text that must be visible, and any LoRA trigger words, exactly as they are.
Output only the prompt."""


def rewrite_system_for(target):
    """The system prompt of the Rewrite node for a target (shown in the node so the user can change it)."""
    g = guides()[target]
    kind = "video" if g["video"] else "image"
    return (f"{REWRITE}\n\nTARGET: {target} ({kind}).\n\nFORMAT RULES OF THE TARGET MODEL:\n{g['guide']}\n\n"
            f"{COMMON_RULES}")


def rewrite_series(prompts, target, generate, frame="Landscape 16:9", seed=42, system=None, log=print):
    """Each prompt rewritten for the target. generate(system, message, seed) -> text."""
    if target not in guides():
        raise ValueError(f"Unknown target model '{target}'. Pick one from the list.")
    system = (system or "").strip() or rewrite_system_for(target)
    settings = FRAME_SETTINGS.get(frame, FRAME_SETTINGS["Landscape 16:9"])
    rng = random.Random(int(seed))
    out = []
    for i, prompt in enumerate(prompts):
        message = f"TASK: IMPROVE\nSETTINGS: {settings}\nCURRENT PROMPT: {prompt}"
        text = clean_output(generate(system, message, rng.randint(0, 2 ** 31 - 1)))
        if not text:
            raise RuntimeError(f"The text model gave an empty answer rewriting prompt {i + 1} for {target}.")
        log(f"[Vidlamp Prompt Rewrite] {target} {i + 1}/{len(prompts)}: {text[:90]}...")
        out.append(text)
    return out


def as_written(idea, trigger_words=""):
    """The user's own prompts, word for word: one per paragraph (an empty line between them). 10/11/2026, the user:
    "if the user writes his prompt it is always reworked: let him switch the generator off and test his prompt"."""
    parts = [p.strip() for p in re.split(r"\n\s*\n", str(idea or "").strip()) if p.strip()]
    if not parts:
        raise ValueError("'Use my text as the prompt' is on but the idea box is empty: write your prompt there "
                         "(several prompts: an empty line between them).")
    tw = str(trigger_words or "").strip()
    return [p if not tw or p.lower().startswith(tw.lower()) else f"{tw}, {p}" for p in parts]
