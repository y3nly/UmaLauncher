const assert = require('node:assert/strict');
const {JSDOM} = require('jsdom');
const scripts = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const dom = new JSDOM('<html><head></head><body></body></html>', {
    runScripts: 'outside-only', pretendToBeVisual: true,
    url: 'https://gametora.com/umamusume/training-event-helper'
});
const w = dom.window;
const d = w.document;
const rows = '<div><div><span>Top</span></div><div><div>Speed +10</div></div></div>'
    + '<div><div><span>Bot</span></div><div><div>Randomly either</div>'
    + '<div>Energy +30</div><div>or</div><div>Get <span class="utils_linkcolor_x">Slow Metabolism</span></div></div></div>';
const current = (title, rewards = rows) => `<section><div><div><h3>${title}</h3><div>Senior Class</div></div>`
    + `<div><div>Conditions: win a race</div><div><div>${rewards}</div></div></div></div></section>`;
const extract = markup => {
    d.body.innerHTML = `<div class="tippy-content"><div>${markup}</div></div>`;
    const state = {activeOutcome: d.body, anchor: {textContent: 'Fallback'}};
    return w.Function('state', 'ownedStatuses', `const extract = () => ${scripts.extraction}; return extract();`)(state, ['Slow Metabolism']);
};
for (const markup of [current('Fixture Event'), `<div><div>Fixture Event</div><div>${rows}</div></div>`]) {
    const event = extract(markup);
    assert.equal(event.title, 'Fixture Event');
    assert.equal(event.outcomes.length, 2);
    assert.equal(event.outcomes[0].label, 'Top');
    assert.equal(event.outcomes[0].lines[0].text, 'Speed +10');
    assert.equal(event.outcomes[1].lines.length, 4);
    assert.equal(event.outcomes[1].lines[3].owned, true);
}
const single = extract(current('No choice', '<div><div><div>Power +5</div></div></div>'));
assert.equal(single.outcomes.length, 1);
assert.equal(single.outcomes[0].label, '');
assert.equal(single.outcomes[0].lines[0].text, 'Power +5');
assert.equal(extract('<section><h3>Loading</h3></section>'), null);

// The source filters are enabled, but race-result controls inside cards stay intact.
d.body.innerHTML = '<input id="allAtOnceCheckbox" type="checkbox" checked>'
    + '<input id="expandEventsCheckbox" type="checkbox">'
    + '<input id="onlyChoicesCheckbox" type="checkbox">'
    + '<div id="viewer-box-main"><div id="event-viewer-0"><div>Fixture Trainee</div>'
    + '<div><input id="common" type="checkbox"><input id="races" type="checkbox"></div>'
    + '<div>Random Events</div><div>' + current('Fixture Event') + current('Other event',
        '<div><div><div>Stamina +5</div></div></div>') + '</div></div></div>';
const race = d.createElement('input');
race.type = 'checkbox'; race.id = 'race-result';
d.querySelector('section').append(race);
w.Function(scripts.configure)();
assert.equal(d.getElementById('common').checked, true);
assert.equal(d.getElementById('races').checked, true);
assert.equal(race.checked, false);
w.eval(scripts.search);
assert.equal(d.getElementById('ul-event-search-count').textContent, '2 events');
assert.equal(d.getElementById('ul-event-search-notice').hidden, true);
w.UL_EVENT_SEARCH_QUERY = 'speed';
w.UL_APPLY_EVENT_SEARCH();
assert.equal(d.getElementById('ul-event-search-count').textContent, '1 result');
assert.equal(d.querySelectorAll('section.ul-event-search-hidden').length, 1);
assert.equal(race.checked, false);
w.UL_EVENT_SEARCH_QUERY = '';
w.UL_APPLY_EVENT_SEARCH();
assert.equal(d.querySelectorAll('section.ul-event-search-hidden').length, 0);
// Execute the shipped formatter, not a copy of its regular expression.
w.eval(`function appendTextElement(parent, tag, classes, text) {
    const node = document.createElement(tag); node.className = classes;
    node.textContent = text; parent.appendChild(node); return node;
}
function appendSemanticTokens${scripts.tokenizer}`);
const colored = d.createElement('div');
w.appendSemanticTokens(colored, 'Speed +10 Energy -5 Mood −1 Gain + 2');
assert.equal(colored.querySelectorAll('.semantic-value.is-positive').length, 2);
assert.equal(colored.querySelectorAll('.semantic-value.is-negative').length, 2);
assert.equal(colored.querySelectorAll('.semantic-gain').length, 1);
assert.equal(colored.textContent, 'Speed +10 Energy -5 Mood −1 Gain + 2');
w.eval(`function safeText(value) { return String(value ?? ''); }
function appendSemanticText${scripts.formatter}`);
for (const [text, tone] of [
    ['Get Charming', 'positive'], ['Get Hot Topic', 'positive'],
    ['Get Slow Metabolism', 'negative'], ['Become Night Owl', 'negative'],
    ['Cures Migraine', 'positive'], ['Heal Practice Poor', 'positive'],
    ['Remove Charming', 'negative'], ['Lose Hot Topic', 'negative'],
]) {
    const line = d.createElement('div');
    w.appendSemanticText(line, text);
    assert.equal(line.querySelector('.semantic-status')?.className, `semantic-status is-${tone}`, text);
    assert.equal(line.textContent, text);
}
dom.window.close();
console.log('GameTora DOM regressions passed');
