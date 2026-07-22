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
    document.querySelectorAll(".packet-prediction[data-ul-private='true']")
      .forEach(element => element.remove());
    document.querySelectorAll(".gametora-outcome.has-packet-prediction")
      .forEach(element => element.classList.remove("has-packet-prediction"));
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
      .packet-reward.is-positive { color: #8ce8aa; }
      .packet-reward.is-negative { color: #ff8c95; }
      .packet-reward.is-hint { color: #ffd66e; }
      .gametora-outcome.has-packet-prediction .gametora-reward-lines {
        display: block;
        max-width: 100%;
        overflow-wrap: anywhere;
        white-space: normal;
      }
    `;
    document.head.appendChild(style);
  }

  function appendTextElement(parent, className, text) {
    const element = document.createElement("div");
    element.className = className;
    element.textContent = safeText(text);
    parent.appendChild(element);
    return element;
  }

  function render() {
    removeRenderedPredictions();
    if (!state.active || !state.prediction || !viewingPacketEvent()) {
      return false;
    }

    const cards = Array.from(
      document.querySelectorAll("#event-fallback .gametora-outcome")
    );
    if (!cards.length) {
      return false;
    }

    ensureStyle();
    const choices = new Map(
      state.prediction.choices.map(choice => [choice.choiceNumber, choice])
    );
    cards.forEach((card, index) => {
      const choice = choices.get(index + 1);
      if (!choice) {
        return;
      }

      const prediction = document.createElement("div");
      prediction.className = "packet-prediction";
      prediction.dataset.ulPrivate = "true";
      appendTextElement(prediction, "packet-prediction-label", "Prediction");

      const rewards = choice.rewards.length
        ? choice.rewards
        : [{
          text: choice.summary || `Outcome ${choice.outcomeIndex || 1}`,
          tone: "neutral"
        }];
      rewards.forEach(reward => {
        const line = appendTextElement(prediction, "packet-reward", reward.text);
        if (["positive", "negative", "hint"].includes(reward.tone)) {
          line.classList.add(`is-${reward.tone}`);
        }
      });

      const rewardLines = card.querySelector(":scope > .gametora-reward-lines");
      card.insertBefore(prediction, rewardLines || card.firstChild);
      card.classList.add("has-packet-prediction");
    });
    return true;
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
