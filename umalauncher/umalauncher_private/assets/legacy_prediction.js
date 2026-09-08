(function(anchor, prediction) {
    if (!prediction || !Array.isArray(prediction.choices)) return;
    const eventState = document.getElementById("ul-event-focus-state");
    if (
        !anchor
        && eventState?.anchor
        && eventState.anchor === eventState.packetAnchor
    ) {
        anchor = eventState.packetAnchor;
    }
    // Packet predictions annotate only the actual current event,
    // never another chain card the user is previewing.
    if (!anchor) return;

    if (!document.getElementById("ul-choice-reward-style")) {
        const style = document.createElement("style");
        style.id = "ul-choice-reward-style";
        style.textContent = `
            .ul-choice-reward-inline {
                margin-top: 0.25rem;
                padding: 0.2rem 0.45rem;
                border-left: 3px solid #60a5fa;
                border-radius: 3px;
                background: rgba(37, 99, 235, 0.14);
                color: #f8fafc;
                display: flex;
                flex-wrap: wrap;
                align-items: baseline;
                font-size: 12px;
                font-weight: 700;
                line-height: 1.35;
                box-sizing: border-box;
                width: fit-content;
                max-width: 100%;
            }
            .ul-choice-reward-label {
                color: #ffffff;
                font-weight: 800;
                margin-right: 0.25rem;
            }
            .ul-choice-reward-text {
                color: #f8fafc;
            }
            .ul-choice-reward-separator {
                color: #cbd5e1;
                margin-right: 0.25rem;
            }
            .ul-choice-reward-positive {
                color: #86efac;
                margin-left: 0.15rem;
            }
            .ul-choice-reward-negative {
                color: #fca5a5;
                margin-left: 0.15rem;
            }
            .ul-choice-reward-skill {
                color: #fbbf24;
            }
        `;
        document.head.appendChild(style);
    }

    function isVisible(el, rect = el?.getBoundingClientRect()) {
        if (!el) return false;
        if (rect.width <= 0 || rect.height <= 0) return false;
        const style = window.getComputedStyle(el);
        return style.display !== "none" && style.visibility !== "hidden";
    }

    function ownText(el) {
        return Array.from(el.childNodes)
            .filter(node => node.nodeType === Node.TEXT_NODE)
            .map(node => node.textContent.trim())
            .filter(Boolean)
            .join(" ");
    }

    function normalizedText(el) {
        const direct = ownText(el);
        const text = direct || (el.children.length === 0 ? el.textContent : "");
        return (text || "").replace(/\s+/g, " ").trim().toLowerCase();
    }

    function choiceLabels(index, count) {
        if (count === 2) {
            return [
                ["top", "1."],
                ["bot", "2."]
            ][index - 1] || [`${index}.`];
        }
        return [
            ["top", "1."],
            ["mid", "2."],
            ["bot", "3."],
            ["4."],
            ["5."],
            ["6."]
        ][index - 1] || [`${index}.`];
    }

    function appendSignedText(parent, text) {
        const parts = String(text || "").split(/([+-]\d+(?:\.\d+)?%?)/g);
        for (const part of parts) {
            if (!part) continue;

            const span = document.createElement("span");
            span.textContent = part;
            if (/^\+\d/.test(part)) {
                span.className = "ul-choice-reward-positive";
            } else if (/^-\d/.test(part)) {
                span.className = "ul-choice-reward-negative";
            } else {
                span.className = "ul-choice-reward-text";
            }
            parent.appendChild(span);
        }
    }

    function appendSkillHighlight(parent, text) {
        const span = document.createElement("span");
        span.className = "ul-choice-reward-skill";
        span.textContent = text;
        parent.appendChild(span);
    }

    function appendRewardText(parent, reward) {
        const text = String(reward?.text || "");
        const highlights = Array.isArray(reward?.highlights)
            ? reward.highlights.filter(Boolean)
            : [];

        if (!highlights.length) {
            appendSignedText(parent, text);
            return;
        }

        let remaining = text;
        while (remaining) {
            let nextMatch = null;
            for (const highlight of highlights) {
                const index = remaining.indexOf(highlight);
                if (index >= 0 && (!nextMatch || index < nextMatch.index)) {
                    nextMatch = { index, highlight };
                }
            }

            if (!nextMatch) {
                appendSignedText(parent, remaining);
                break;
            }

            if (nextMatch.index > 0) {
                appendSignedText(parent, remaining.slice(0, nextMatch.index));
            }
            appendSkillHighlight(parent, nextMatch.highlight);
            remaining = remaining.slice(nextMatch.index + nextMatch.highlight.length);
        }
    }

    function renderPrediction(parent, choice) {
        const label = document.createElement("span");
        label.className = "ul-choice-reward-label";
        label.textContent = "Predicted:";
        parent.appendChild(label);

        const rewards = Array.isArray(choice.rewards) && choice.rewards.length
            ? choice.rewards
            : [{ text: choice.summary || `Outcome ${choice.outcome_index || 1}` }];

        rewards.forEach((reward, index) => {
            if (index > 0) {
                const separator = document.createElement("span");
                separator.className = "ul-choice-reward-separator";
                separator.textContent = ", ";
                parent.appendChild(separator);
            }
            appendRewardText(parent, reward);
        });
    }

    function findTooltipRoot() {
        const scope = anchor?.parentElement || document;
        const roots = scope.querySelectorAll("div[data-tippy-root]");
        for (let index = roots.length - 1; index >= 0; index--) {
            if (isVisible(roots[index])) return roots[index];
        }

        const globalRoots = document.querySelectorAll("div[data-tippy-root]");
        for (let index = globalRoots.length - 1; index >= 0; index--) {
            if (isVisible(globalRoots[index])) return globalRoots[index];
        }

        return anchor?.closest("div[id^='event-viewer-'], div[class^='compatibility_result_box_']")
            || anchor?.parentElement
            || document.body;
    }

    function labelTarget(labelEl) {
        let current = labelEl;
        for (let depth = 0; depth < 4 && current; depth++) {
            const parent = current.parentElement;
            if (!parent) return null;

            if (current.nextElementSibling) {
                return { container: current.nextElementSibling };
            }

            current = parent;
        }

        return null;
    }

    function uniqueTargets(targets) {
        const seen = new Set();
        const unique = [];
        for (const target of targets) {
            if (!target?.container) continue;
            if (seen.has(target.container)) continue;
            seen.add(target.container);
            unique.push({ target, rect: target.container.getBoundingClientRect() });
        }
        unique.sort((a, b) => {
            return (a.rect.top - b.rect.top) || (a.rect.left - b.rect.left);
        });
        return unique.map(entry => entry.target);
    }

    function findStructuralChoiceTargets(nodes) {
        const labelWords = new Set([
            "top", "mid", "bot",
            "1.", "2.", "3.", "4.", "5.", "6."
        ]);

        const labelTargets = nodes
            .filter(({ node, text }) => {
                if (!labelWords.has(text)) return false;
                const rect = node.getBoundingClientRect();
                return rect.width <= 120 && rect.height <= 80 && isVisible(node, rect);
            })
            .map(({ node }) => labelTarget(node));

        return uniqueTargets(labelTargets);
    }

    function findChoiceTargets(root, count) {
        const nodes = Array.from(root.querySelectorAll("div, span"), node => ({
            node,
            text: normalizedText(node)
        }));
        const used = new Set();
        const targets = [];

        for (let index = 1; index <= count; index++) {
            const expected = choiceLabels(index, count);
            const labelNode = nodes.find(({ node, text }) => (
                !used.has(node) && expected.includes(text)
            ))?.node;
            if (!labelNode) {
                targets.push(null);
                continue;
            }

            used.add(labelNode);
            targets.push(labelTarget(labelNode));
        }

        if (targets.every(target => target?.container)) {
            return targets;
        }

        const structuralTargets = findStructuralChoiceTargets(nodes);
        if (structuralTargets.length >= count) {
            return structuralTargets.slice(0, count);
        }

        return targets;
    }

    const root = findTooltipRoot();
    if (!root) return;

    root.querySelectorAll(".ul-choice-reward-inline").forEach(el => el.remove());

    const choices = prediction.choices.filter(choice => choice && choice.choice_number);
    const targets = findChoiceTargets(root, choices.length);

    for (let i = 0; i < choices.length; i++) {
        const predictionEl = document.createElement("div");
        predictionEl.className = "ul-choice-reward-inline";
        renderPrediction(predictionEl, choices[i]);

        const target = targets[i];
        if (target?.container) {
            target.container.appendChild(predictionEl);
        } else {
            root.appendChild(predictionEl);
        }
    }
})(arguments[0], arguments[1]);
