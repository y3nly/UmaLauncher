(function installPrivateEventPredictions() {
  "use strict";

  const STATE_KEY = "__UL_PRIVATE_EVENT_PREDICTION__";
  if (window[STATE_KEY]?.installed) {
    return true;
  }

  const state = {
    installed: true,
    active: false,
    generation: null,
    prediction: null,
    chain: null
  };
  window[STATE_KEY] = state;
  let lastRender = null;

  function safeText(value, limit = 500) {
    return String(value ?? "").replace(/\0/g, "").slice(0, limit);
  }

  function safeInteger(value) {
    const number = Number(value);
    return Number.isSafeInteger(number) ? number : null;
  }

  function normalizeChain(chain) {
    if (!chain || typeof chain !== "object") {
      return null;
    }
    const index = safeInteger(chain.index);
    const packetIndex = safeInteger(chain.packetIndex ?? chain.packet_index);
    return {
      index: index === null ? 0 : index,
      packetIndex: packetIndex === null ? (index === null ? 0 : index) : packetIndex
    };
  }

  function normalizePrediction(prediction) {
    if (!prediction || typeof prediction !== "object") {
      return null;
    }
    const rawChoices = Array.isArray(prediction.choices)
      ? prediction.choices.slice(0, 8)
      : [];
    const choices = rawChoices.flatMap((choice, index) => {
      if (!choice || typeof choice !== "object") {
        return [];
      }
      const rawNumber = safeInteger(choice.choice_number);
      const choiceNumber = rawNumber !== null && rawNumber >= 1 && rawNumber <= 8
        ? rawNumber
        : index + 1;
      const rewards = (Array.isArray(choice.rewards) ? choice.rewards : [])
        .slice(0, 16)
        .flatMap(reward => {
          if (!reward || typeof reward !== "object") {
            return [];
          }
          const tone = ["positive", "negative", "hint", "neutral"].includes(reward.tone)
            ? reward.tone
            : "neutral";
          return [{
            text: safeText(reward.text),
            tone,
            highlights: (Array.isArray(reward.highlights) ? reward.highlights : [])
              .slice(0, 8)
              .map(value => safeText(value, 120))
              .filter(Boolean)
          }];
        });
      return [{
        choiceNumber,
        summary: safeText(choice.summary),
        outcomeIndex: safeInteger(choice.outcome_index),
        rewards
      }];
    });
    return {choices};
  }

  function generationMatches(generation) {
    const candidate = safeInteger(generation);
    return candidate !== null && candidate === state.generation;
  }

  function viewingPacketEvent() {
    return !state.chain || state.chain.index === state.chain.packetIndex;
  }

  function removeRenderedPredictions() {
    lastRender = null;
    const fallback = document.getElementById("event-fallback");
    if (!fallback) {
      return;
    }
    fallback.querySelectorAll(".packet-prediction[data-ul-private='true']")
      .forEach(element => element.remove());
    fallback.querySelectorAll(".gametora-outcomes.has-packet-predictions")
      .forEach(element => element.classList.remove("has-packet-predictions"));
  }

  function ensureStyle() {
    if (document.getElementById("ul-private-event-prediction-style")) {
      return;
    }
    const style = document.createElement("style");
    style.id = "ul-private-event-prediction-style";
    style.textContent = `
      .packet-prediction[data-ul-private="true"] {
        margin: 0 0 5px;
        padding: 4px 4px 4px 5px;
        border-left: 2px solid rgba(255, 168, 83, 0.72);
        border-radius: 3px;
        background: rgba(255, 255, 255, 0.035);
      }
      .packet-prediction-label {
        margin-bottom: 2px;
        color: #d0d5de;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
      }
      .packet-reward { color: var(--text); }
      .packet-reward + .packet-reward { margin-top: 2px; }
      .packet-reward.is-positive:not(.has-signed-value),
      .semantic-skill,
      .semantic-gain,
      .semantic-value.is-positive {
        color: #ff9d54;
      }
      .packet-reward.is-negative:not(.has-signed-value),
      .semantic-value.is-negative {
        color: #69b7ff;
      }
      .has-packet-predictions .gametora-reward-lines {
        display: block;
        max-width: 100%;
        overflow-wrap: anywhere;
        white-space: normal;
      }
      .has-packet-predictions .gametora-reward-line {
        display: inline;
        margin-top: 0;
        color: var(--text);
      }
      .has-packet-predictions .gametora-reward-line.is-owned {
        color: #7d8594;
      }
      .has-packet-predictions .gametora-reward-line .semantic-skill,
      .has-packet-predictions .gametora-reward-line .semantic-gain,
      .has-packet-predictions .gametora-reward-line .semantic-value {
        color: inherit;
      }
      .has-packet-predictions .gametora-reward-line:not(:last-child)::after {
        content: " ·";
        color: var(--muted);
      }
    `;
    document.head.appendChild(style);
  }

  function appendTextElement(parent, tagName, className, text) {
    const element = document.createElement(tagName);
    element.className = className;
    element.textContent = safeText(text);
    parent.appendChild(element);
    return element;
  }

  function appendSemanticTokens(parent, text) {
    const semanticTokenPattern = /([+-]\d+(?:\.\d+)?%?|\bgains?\b)/gi;
    let cursor = 0;
    let match;
    let hasSignedValue = false;
    while ((match = semanticTokenPattern.exec(text)) !== null) {
      if (match.index > cursor) {
        parent.appendChild(document.createTextNode(text.slice(cursor, match.index)));
      }
      if (match[0].startsWith("+") || match[0].startsWith("-")) {
        hasSignedValue = true;
        const valueTone = match[0].startsWith("-") ? "negative" : "positive";
        appendTextElement(
          parent,
          "span",
          `semantic-value is-${valueTone}`,
          match[0]
        );
      } else {
        appendTextElement(parent, "span", "semantic-gain", match[0]);
      }
      cursor = match.index + match[0].length;
    }
    if (cursor < text.length) {
      parent.appendChild(document.createTextNode(text.slice(cursor)));
    }
    return hasSignedValue;
  }

  function appendSemanticText(parent, value, tone = "neutral", highlights = []) {
    const text = safeText(value);
    const highlightSignedValues = tone !== "hint";
    const highlightValues = [...new Set(
      (Array.isArray(highlights) ? highlights : [])
        .map(highlight => safeText(highlight))
        .filter(Boolean)
    )].sort((left, right) => right.length - left.length);
    const highlightRanges = [];
    highlightValues.forEach(highlight => {
      let fromIndex = 0;
      while (fromIndex < text.length) {
        const index = text.indexOf(highlight, fromIndex);
        if (index < 0) {
          break;
        }
        const end = index + highlight.length;
        const overlaps = highlightRanges.some(range => (
          index < range.end && end > range.index
        ));
        if (!overlaps) {
          highlightRanges.push({index, end, text: highlight});
        }
        fromIndex = end;
      }
    });
    highlightRanges.sort((left, right) => left.index - right.index);

    let cursor = 0;
    let hasSignedValue = false;
    highlightRanges.forEach(range => {
      if (range.index > cursor) {
        const leadingText = text.slice(cursor, range.index);
        if (highlightSignedValues) {
          hasSignedValue = appendSemanticTokens(parent, leadingText)
            || hasSignedValue;
        } else {
          parent.appendChild(document.createTextNode(leadingText));
        }
      }
      appendTextElement(parent, "span", "semantic-skill", range.text);
      cursor = range.end;
    });
    if (cursor < text.length) {
      const trailingText = text.slice(cursor);
      if (highlightSignedValues) {
        hasSignedValue = appendSemanticTokens(parent, trailingText)
          || hasSignedValue;
      } else {
        parent.appendChild(document.createTextNode(trailingText));
      }
    }
    if (hasSignedValue) {
      parent.classList.add("has-signed-value");
    }
    if (["positive", "negative", "hint"].includes(tone)) {
      parent.classList.add(`is-${tone}`);
    }
  }

  function render() {
    if (!state.active || !state.prediction || !viewingPacketEvent()) {
      removeRenderedPredictions();
      return false;
    }

    const cards = Array.from(
      document.querySelectorAll("#event-fallback .gametora-outcome")
    );
    const predictionKey = JSON.stringify(state.prediction);
    // Chain navigation can resend rewards immediately after rebuilding the
    // event. Reuse annotations only while both their data and DOM are current.
    if (
      lastRender?.predictionKey === predictionKey
      && cards.length === lastRender.cards.length
      && cards.every((card, index) => card === lastRender.cards[index])
      && lastRender.elements.every(element => element.isConnected)
    ) {
      return true;
    }

    removeRenderedPredictions();
    if (!cards.length) {
      return false;
    }

    ensureStyle();
    const choices = new Map(
      state.prediction.choices.map(choice => [choice.choiceNumber, choice])
    );
    const renderedElements = [];
    cards.forEach((card, index) => {
      const choice = choices.get(index + 1);
      if (!choice) {
        return;
      }

      const rewards = choice.rewards;
      if (!rewards.length && !choice.summary) {
        return;
      }

      const prediction = document.createElement("div");
      prediction.className = "packet-prediction";
      prediction.dataset.ulPrivate = "true";
      appendTextElement(
        prediction,
        "div",
        "packet-prediction-label",
        "Prediction"
      );

      if (rewards.length) {
        rewards.forEach(reward => {
          const line = document.createElement("div");
          line.className = "packet-reward";
          appendSemanticText(
            line,
            reward.text,
            reward.tone,
            reward.highlights
          );
          prediction.appendChild(line);
        });
      } else {
        const line = document.createElement("div");
        line.className = "packet-reward";
        appendSemanticText(line, choice.summary);
        prediction.appendChild(line);
      }

      const rewardLines = card.querySelector(":scope > .gametora-reward-lines");
      card.insertBefore(prediction, rewardLines || card.firstChild);
      const outcomes = card.closest(".gametora-outcomes");
      if (outcomes) {
        outcomes.classList.add("has-packet-predictions");
      }
      renderedElements.push(prediction);
    });
    if (renderedElements.length) {
      lastRender = { predictionKey, cards, elements: renderedElements };
      return true;
    }
    return false;
  }

  const originalOpen = window.UL_OPEN_EVENT_DRAWER;
  if (typeof originalOpen === "function") {
    window.UL_OPEN_EVENT_DRAWER = function privateOpenEventDrawer(payload = {}) {
      const result = originalOpen.apply(this, arguments);
      if (!result) {
        return result;
      }
      const generation = safeInteger(payload?.generation);
      const newGeneration = generation !== state.generation;
      state.active = true;
      state.generation = generation;
      state.chain = normalizeChain(payload?.chain);
      if (newGeneration) {
        state.prediction = null;
      }
      render();
      return result;
    };
  }

  const originalClose = window.UL_CLOSE_EVENT_DRAWER;
  if (typeof originalClose === "function") {
    window.UL_CLOSE_EVENT_DRAWER = function privateCloseEventDrawer(generation) {
      const result = originalClose.apply(this, arguments);
      if (result && generationMatches(generation)) {
        state.active = false;
        state.prediction = null;
        state.chain = null;
        removeRenderedPredictions();
      }
      return result;
    };
  }

  const originalSetEvent = window.UL_SET_GAMETORA_EVENT;
  if (typeof originalSetEvent === "function") {
    window.UL_SET_GAMETORA_EVENT = function privateSetGametoraEvent(
      event,
      chain,
      generation
    ) {
      const result = originalSetEvent.apply(this, arguments);
      if (result && generationMatches(generation)) {
        state.chain = normalizeChain(chain);
        render();
      }
      return result;
    };
  }

  window.UL_CLEAR_EVENT_REWARDS = function UL_CLEAR_EVENT_REWARDS(generation) {
    if (!state.active || !generationMatches(generation)) {
      return false;
    }
    removeRenderedPredictions();
    return true;
  };

  window.UL_UPDATE_EVENT_REWARDS = function UL_UPDATE_EVENT_REWARDS(
    generation,
    prediction
  ) {
    if (!state.active || !generationMatches(generation)) {
      return false;
    }
    state.prediction = normalizePrediction(prediction);
    return render();
  };

  return true;
})();
