# Skill purchase planning

The rating helper uses Umapyoi's final-purchase policy. Its recommendation remains
advisory; it does not send skill purchases to the game.

Required selections reserve an affordable minimum rank in selection order.
Higher ranks remain eligible. Explicit exclusions, including prerequisites,
remain absolute and report a conflict when needed by a required selection.

The optimizer maximizes these priorities in order:

1. Distinct good skill families.
2. Additional Bashin from good families, including upgrades.
3. Other positive Bashin.
4. Distinct activating greens, then their rank gains.
5. Distinct other activating families, then their rank gains.
6. Additional rating with the remaining budget.

Good families meet half the weaker style Corners/Straightaways single-circle
Bashin/SP benchmark. The benchmark is undiscounted; classification uses discounted
full acquisition costs and carries goodness into higher ranks. Required normal
families also qualify. Purchase prices exclude owned ranks and apply each hint
to all ranks of its rarity. Gold replaces the double-circle endpoint where that
branch is available; its price still includes unpaid lower ranks.

SS is 17,500 rating. Grouped knapsack compares one endpoint per family under the
SP budget. Rating bounds first establish whether SS is reachable without inherited
skills. Only if it is impossible without them are inherited skills admitted as
SS-only rating purchases, with zero Bashin and activation credit. If SS remains
impossible, inherited skills stay excluded and the normal priority order still
spends the remaining SP. It never stops spending merely because SS is unreachable.

The optimizer first finds the best priority plan. If SS is feasible but that plan
misses SS, it repeats the optimization with SS required. Required ranks remain
mandatory. Equivalent plans prefer fewer SS exceptions, lower exception costs,
then lower total cost and stable IDs. Fallback purchases are ordered within their
tiers by rating gained per SP.

`umalauncher/skill_purchase_optimizer.py` is identical to Umapyoi's
`core/utils/skill_purchase_optimizer.py`; keep both copies synchronized. The
activation helper is mirrored too. Observed effective rates or supported master
conditions qualify activating skills. Unknown activation only qualifies for the
last rating fallback. Each app keeps its own packet/master and performance-data
adapter, so different published Bashin inputs can produce different selections.
