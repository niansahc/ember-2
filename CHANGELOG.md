# Changelog

## [0.15.0](https://github.com/niansahc/ember-2/compare/v0.14.0...v0.15.0) (2026-04-20)


### ⚠ BREAKING CHANGES

* **safety:** rewrite flourishing_over_preference as v0.2, bump constitution to v0.7

### Features

* add business hours push protection hook and GitHub Actions check ([1955662](https://github.com/niansahc/ember-2/commit/19556623b31881dcc7b9aeb05ef96cf0f3cc1eb3))
* **api:** add cross-platform watchdog for API restart and stop ([377c5e1](https://github.com/niansahc/ember-2/commit/377c5e1bad50d8d02df808f4ecf40002263c34d0))
* **api:** add disk encryption status endpoint ([b210ae1](https://github.com/niansahc/ember-2/commit/b210ae136490f2f2fc6e9ac02af215eef3bb73a2))
* **api:** add service health, restart, and developer status endpoints ([13600a5](https://github.com/niansahc/ember-2/commit/13600a514271f17ba639d93e38bcef4434355fa9))
* **api:** add vault storage endpoint with usage projection ([e5cf98f](https://github.com/niansahc/ember-2/commit/e5cf98fecadce225361ff14689be09b566efa9d1))
* **api:** add web_search_autonomous preference field (default False) ([d463003](https://github.com/niansahc/ember-2/commit/d4630036e2789f59b2fad1cdc002639c617709e4))
* **api:** emit vault citation signal — X-Ember-Vault-Used header and vault_sources SSE event ([41d2a45](https://github.com/niansahc/ember-2/commit/41d2a45e955fbddaeda4315676139193324bc699))
* **api:** implement bare mode backend with reduced pipeline ([e59657b](https://github.com/niansahc/ember-2/commit/e59657be0eb9e349b7a526fa734f777b78749e3b))
* **api:** implement per-conversation vault toggle — stateless mode ([5fe8df6](https://github.com/niansahc/ember-2/commit/5fe8df60660c15e8a4a64b14856e94de77747fd0))
* **backend:** timer functions via state layer (BUG-004) ([5d283de](https://github.com/niansahc/ember-2/commit/5d283de383ff05140440d209a29555f3663cefac))
* configure vault protection hook and document in CLAUDE.md ([27957df](https://github.com/niansahc/ember-2/commit/27957df795322487e311187e6a0dc756131b2445))
* **constitution:** file flourishing_over_preference v0.1 — scoped to within-session, amplification risk documented ([17e4524](https://github.com/niansahc/ember-2/commit/17e4524bfbc9a6e77cb5c2d09d088ef5aff15445))
* **constitution:** file relational_honesty v0.5 with trigger conditions and behavioral sequence ([38c3271](https://github.com/niansahc/ember-2/commit/38c32713ede87b0bd71f89f649a9ff607428738d))
* **developer:** add runtime vault swap endpoint for dev mode ([2ca3516](https://github.com/niansahc/ember-2/commit/2ca35168edb7593130d5d2aaaecc7ffc6be50ec5))
* **eval:** add --compare flag using cloud provider dispatch for Haiku ([df83f62](https://github.com/niansahc/ember-2/commit/df83f6268c0f91e0e53df3ede71e6139e3c1fc05))
* **eval:** add automated web search accuracy eval with multi-model comparison ([b7ed6cc](https://github.com/niansahc/ember-2/commit/b7ed6cc7b94497c42db2a4ef66eeef3b624b2c3f))
* **eval:** add contextual integrity cases to retrieval eval benchmark ([6f3a537](https://github.com/niansahc/ember-2/commit/6f3a5376072fbd3d37e5bc0abfbdaf2ec3a6647c))
* **eval:** add conversation quality eval framework with LLM-as-judge ([e083bca](https://github.com/niansahc/ember-2/commit/e083bca3fbe277eb0673c27e63d7aad841ede07a))
* **eval:** add four Alex-specific golden cases with realistic profile context ([0bba49a](https://github.com/niansahc/ember-2/commit/0bba49a17b161c0b16d227a6485e39de1ebbfaa6))
* **eval:** add multi-run averaging for non-deterministic model evaluation ([22f6296](https://github.com/niansahc/ember-2/commit/22f6296f17c84131c9f05a9aea42073c84b81f7e))
* **eval:** add three golden cases — mixed content, relational overclaiming, thin vault ([399f3a9](https://github.com/niansahc/ember-2/commit/399f3a9e22229dbb16bc817fd173a0203324704a))
* **hooks:** add vault protection and test hooks ([f63a514](https://github.com/niansahc/ember-2/commit/f63a514af46f8eee6c1a2ead54eec4d00c34a472))
* **identity:** add stance-level identity rules and nature orientations to address template collapse and deflection patterns ([ab547f8](https://github.com/niansahc/ember-2/commit/ab547f82d5956cfe9aa18c078b8d2f2ae77013d0))
* **ingest:** ChatGPT role normalization + ingested retrieval scoring fix ([7a372a6](https://github.com/niansahc/ember-2/commit/7a372a6dbdae083c2e0caaca18ef7f0bb6e15349))
* **llm:** add stage 2 semantic rewrite for identity collapse ([e0de854](https://github.com/niansahc/ember-2/commit/e0de854ca109c38e775096779d3c7138576d61f5))
* **llm:** add two-stage post-generation coaching-frame filter ([0c3d009](https://github.com/niansahc/ember-2/commit/0c3d009f2961c54a606c23f986c7a024cc634606))
* **nature:** add aesthetic specificity to curiosity and engagement facets ([0201b6c](https://github.com/niansahc/ember-2/commit/0201b6cb7ba79b913c32a42f8bb7801123a7f70a))
* **prompt:** add contrastive few-shot examples for preference expression to instruction rules section ([8eacfa6](https://github.com/niansahc/ember-2/commit/8eacfa6266bd3c1af8c35580c2344426cb3c98ac))
* **prompt:** inject retrieval confidence metadata for hallucination reduction ([a81f73f](https://github.com/niansahc/ember-2/commit/a81f73ff9c283fa06bb12333e0d327ee2919225c))
* **retrieval:** add entity-type web search triggers (Layer 1) ([529bac3](https://github.com/niansahc/ember-2/commit/529bac3d1bb17ef45c84d1ed180dec457b85b6a0))
* **retrieval:** add implicit recency and episodic domain web search triggers ([77b3126](https://github.com/niansahc/ember-2/commit/77b31267d025c94502b91f23981533b54767353f))
* **retrieval:** broaden web search triggers — temporal currency, factual uncertainty, knowledge-gap injection ([c6f467a](https://github.com/niansahc/ember-2/commit/c6f467a5dfb4745c143befc1a08f9c01fbe16497))
* **retrieval:** multiplicative temporal decay weighting in ContextRanker ([1c64f4c](https://github.com/niansahc/ember-2/commit/1c64f4c2726517bc00ba559556fcfb2f1ac375cd))
* **safety:** add validation_before_correction detector to constitutional review trigger layer ([155605c](https://github.com/niansahc/ember-2/commit/155605c72636811133455d55354c98930503a0ac))
* **safety:** identity_challenge trigger to catch position collapse under pressure ([fe5c150](https://github.com/niansahc/ember-2/commit/fe5c150bc88dd04c02a3a38c28b86e235cdb4628))
* **safety:** implement relational intensity amplification gate — suppress lodestone relational records during relational triggers ([60eae41](https://github.com/niansahc/ember-2/commit/60eae411f0ff890d64f9a201aa7c938e86b32592))
* **safety:** minimum viable review prompt with trigger-signal principle append ([cba4efa](https://github.com/niansahc/ember-2/commit/cba4efafc1cabe32330b8abec9018e580bf446c1))
* **safety:** rewrite flourishing_over_preference as v0.2, bump constitution to v0.7 ([030af0f](https://github.com/niansahc/ember-2/commit/030af0f58b960a9637542dbb3d192b2e7a9f6f7f))
* **safety:** wire trigger signals for relational_honesty and flourishing_over_preference principles ([d38ad47](https://github.com/niansahc/ember-2/commit/d38ad47ee6c4638f4488463859f96a14f75f0317))
* **scripts:** add CLI UAT runner with full feature test plan and history logging ([16973a2](https://github.com/niansahc/ember-2/commit/16973a265062e9739bb4d3f43046defc7f95aea3))
* **scripts:** add test artifact cleanup script with dry-run ([533a0a3](https://github.com/niansahc/ember-2/commit/533a0a37b2fa8215b55d2e450de03107e75b8cbe))
* **search:** explicit search invocation bypasses ask-first confirmation ([bf8807c](https://github.com/niansahc/ember-2/commit/bf8807c980a9c763a419811461fc6b698b543147))
* **security:** add PIN change endpoint ([05b5581](https://github.com/niansahc/ember-2/commit/05b5581682f8a08ac95db24b5226ae7e988a5f28))
* **settings:** add context length control ([ad2c09f](https://github.com/niansahc/ember-2/commit/ad2c09f2cc42bb8003e6901447e0a27cad900851))
* strip think blocks, clean few-shot examples, add auto battery mode ([3b50b31](https://github.com/niansahc/ember-2/commit/3b50b31734a462b6acfd53bef8c75b8d1d3827fc))
* **test-vault:** add synthetic Alex profile and memory records ([a42d7ee](https://github.com/niansahc/ember-2/commit/a42d7eeaa16bbcfd4dfc1fa77398ff3d0ff43bd8))
* **tools:** allow multiple annotation codes per response in manual eval CLI ([5f0e265](https://github.com/niansahc/ember-2/commit/5f0e26520541661d2fde7852959a9ef90a174b1d))
* **vault:** create DEVEmberVault structure with demo and test vaults ([0557b15](https://github.com/niansahc/ember-2/commit/0557b15b62793623fef1d1c1c81c654a6f977f1d))
* **web-search:** autonomous default, marker classification split ([8ab4bcd](https://github.com/niansahc/ember-2/commit/8ab4bcd0e093b830ce4f1bbf26fce67a53750bf3))


### Bug Fixes

* **api:** add default vault to swap allowlist ([cc798be](https://github.com/niansahc/ember-2/commit/cc798be5b768d29f8233aaa908b97976bb2f8108))
* **api:** gate all vault-writing paths on X-Test-Session ([c941803](https://github.com/niansahc/ember-2/commit/c941803ed50e513314c2eb33d1d9cbee7359eb8a))
* **api:** preserve response casing in ThinkBlockFilter (BUG-010) ([9a62393](https://github.com/niansahc/ember-2/commit/9a62393282592bfb52ef01e0197034cafbf09010))
* **api:** resolve NameError in streaming SSE — prompt_builder not in scope ([7c8f11c](https://github.com/niansahc/ember-2/commit/7c8f11c82c7567f5dab5fc43ffb7765a03b7a9ed))
* **api:** resolve prompt_builder scope error in _post_stream_cleanup ([f5d72d2](https://github.com/niansahc/ember-2/commit/f5d72d2ffbee6486621bd5e220bed6abbbc335b0))
* **api:** skip all vault writes for test sessions (X-Test-Session) ([bfe3cdd](https://github.com/niansahc/ember-2/commit/bfe3cdd4bced483c5c6f71036b3043966bf9efc9))
* **api:** surface state items in vault sources, thread ask-first ([2c0f048](https://github.com/niansahc/ember-2/commit/2c0f048183a36ed0a66c999662d1ef7c7d5efc17))
* **ask-first:** explicit search bypass in post-gen validator + fix unicode crash ([c1c742c](https://github.com/niansahc/ember-2/commit/c1c742cfe9e0bf79cb2152093ea2fda73dc8f6ef))
* **ask-first:** gate bypass, prefill, deterministic YES/NO, duplicate guard ([bb347e0](https://github.com/niansahc/ember-2/commit/bb347e0c405c22722755896aead730651d2694a1))
* **ask-first:** remove confirmation bypass from context search gate ([c48a633](https://github.com/niansahc/ember-2/commit/c48a633d9b0574c05f604c633021233413176678))
* **backend:** apply timestamp collision guard to write_memory.py ([e017b00](https://github.com/niansahc/ember-2/commit/e017b0001120ae7007691596f565b07b23bd24bf))
* **backend:** guard timestamp-id generators against same-microsecond collisions (BUG-005) ([1d0aa0c](https://github.com/niansahc/ember-2/commit/1d0aa0c05f6b2e2a3dc535ec0dd376185281a181))
* **backend:** inject active project name into prompt context (BUG-002) ([60565ad](https://github.com/niansahc/ember-2/commit/60565ad22203af7fa7f93ce77eca1584b4522b87))
* **backend:** inject inter-session time gap into prompt context (BUG-003) ([642114d](https://github.com/niansahc/ember-2/commit/642114d23fafb9009e642ef6c6d8717330323e74))
* **bug-012:** remove turn label injection from prompt builder ([b7a1a84](https://github.com/niansahc/ember-2/commit/b7a1a847da328f8af66aa3fdb5cebde9861f38f3))
* **docs:** correct Q1 annotation in 2026-04-11 manual eval — a → ah ([3eb7356](https://github.com/niansahc/ember-2/commit/3eb7356923a20e00db1cd2adab33cd23d816e707))
* **eval:** add anti-anchoring instruction to flag detection call ([9e003c4](https://github.com/niansahc/ember-2/commit/9e003c485fd59066bfa40681cd7d2733a441e966))
* **eval:** auto battery saves metadata only — response text not written to disk ([8a30cf2](https://github.com/niansahc/ember-2/commit/8a30cf2d8fa5d87131f321d2547751967ec426db))
* **eval:** auto-cleanup after runs, prompt on manual, use test vault ([99a9f09](https://github.com/niansahc/ember-2/commit/99a9f0910482eb59c7d6df299319e2683fcb12b3))
* **eval:** capture reasoning from flag detection call ([b7310c1](https://github.com/niansahc/ember-2/commit/b7310c11fe86e2f0eabb90d1319509b54557d5bc))
* **eval:** cleanup script purges existing sidebar artifacts ([8095385](https://github.com/niansahc/ember-2/commit/8095385088d1314df096b5628fd34d4847bb456e))
* **eval:** cleanup script removes eval harness sidebar artifacts ([c9234e2](https://github.com/niansahc/ember-2/commit/c9234e2af53de526cec312cc03a1fea2a6d2ea7f))
* **eval:** gate flag failures on minimum 2 fires, not 30% rate ([55da70b](https://github.com/niansahc/ember-2/commit/55da70bf98c57833d4d96da025fa8050a67e734d))
* **eval:** mark eval tests as tier 4, exclude from standard suite ([127b27d](https://github.com/niansahc/ember-2/commit/127b27d6c78a32a5d27c0bd574c25c748cb8b929))
* **eval:** remove vault privacy violation from eval_conversations.py ([cc67a58](https://github.com/niansahc/ember-2/commit/cc67a58211df6367225f09fe304606ce93473cac))
* **eval:** resolve Anthropic API key from OS credential store in judge and harness ([cbc10fa](https://github.com/niansahc/ember-2/commit/cbc10fa7d9299352c282a5c06a91370928d0b1af))
* **eval:** rework web search eval to use active model, remove Anthropic dependency ([dd7133a](https://github.com/niansahc/ember-2/commit/dd7133a25611edc824de46d72f784b2419d6a872))
* **eval:** rework web search eval, add latency tracking, fix hanging ([17cbce6](https://github.com/niansahc/ember-2/commit/17cbce675db68c3c8c370bacc819029d58b4f58a))
* **eval:** separate flag detection from scoring, add override instruction, tighten adversarial rubric ([8c7375c](https://github.com/niansahc/ember-2/commit/8c7375cab9f95975e1e3fffd7735ff7902fba18e))
* **eval:** update baseline scores; investigate stage 0.5 identity collapse gap ([608909f](https://github.com/niansahc/ember-2/commit/608909f5515cae70a29e338aeebc72aa53bbdb00))
* **eval:** update eval_retrieval mock to return 5-tuple after embedding batching ([fe7e205](https://github.com/niansahc/ember-2/commit/fe7e205094fd1658cc495042090a18ba6f6d9199))
* **identity:** strengthen closing_questions rule, add parenthetical filter (BUG-008) ([61536d1](https://github.com/niansahc/ember-2/commit/61536d1b70d8e646b4b1f7638591fb81a820dd39))
* **llm:** handle unicode italic and case variants in think block stripping ([3097696](https://github.com/niansahc/ember-2/commit/30976960424990e4b77d8da0da7f72289067df70))
* **llm:** reduce temperature on emotional intent to suppress coaching-frame defaults ([41f1465](https://github.com/niansahc/ember-2/commit/41f1465112ae0d861c23a294b5a423b7e1828c17))
* **llm:** strip orphaned think tags to recover from malformed reasoning blocks ([b508d8b](https://github.com/niansahc/ember-2/commit/b508d8b7909ec8918dfff34f36320ff61c734936))
* **prompt:** add anti-embellishment rule to authority rules for personal queries ([c19923c](https://github.com/niansahc/ember-2/commit/c19923c44d4bc38ee9dc2c7aa7dd726afda95dfd))
* **prompt:** establish web search authority and suppress profile-only vault citation during web search ([f341445](https://github.com/niansahc/ember-2/commit/f341445ae814f89ffb9c670d8999d1346cf924d9))
* **prompt:** inject active model name as runtime identity section ([ced7e99](https://github.com/niansahc/ember-2/commit/ced7e99928f1a4eab70467392ec8528093b82e78))
* **prompt:** remove contrastive block — wrong examples causing identity collapse regression ([dd04fbd](https://github.com/niansahc/ember-2/commit/dd04fbd9fd3afa41b3a12648bb5d833fd8384fc8))
* **prompt:** replace coaching-frame examples, add contrastive block, add post-gen checks for capitulation and overclaiming ([d80194e](https://github.com/niansahc/ember-2/commit/d80194e744492c26a99340dcb74c88bf2bf1fc13))
* **prompt:** suppress knowledge gap framing across all three injection paths ([04668b9](https://github.com/niansahc/ember-2/commit/04668b92e564b4c95b27c533ebc51bde6e7ca5a5))
* **prompt:** suppress knowledge gap injection on conversational/emotional queries ([417221f](https://github.com/niansahc/ember-2/commit/417221fc345617cc76a56579eafacc74d71bd64a))
* **retrieval:** add journal to reflective intent eligible types — contextual integrity tuning ([5ef471f](https://github.com/niansahc/ember-2/commit/5ef471f39a46bea279029680b059ce031fb4ec6a))
* **retrieval:** add version/release triggers, vault citation threshold, anti-disclaimer rule, launch-installer endpoint ([7af2440](https://github.com/niansahc/ember-2/commit/7af2440a142e9a6d33eef4c8d64c70102d500ced))
* **retrieval:** quarantine AI system documentation from web results ([b6eaedd](https://github.com/niansahc/ember-2/commit/b6eaeddca3b76c72319db09bc07377b3d7406b57))
* **safety:** heuristic numeric fabrication detector in constitutional review ([8474924](https://github.com/niansahc/ember-2/commit/8474924340c600ae35329eb5abc35bba3cf52f1e))
* **safety:** pre-generation override detection and instruction hierarchy defense ([26102ba](https://github.com/niansahc/ember-2/commit/26102ba8383874bd412776aca58c893197365388))
* **safety:** use word boundary matching for short keywords in constitutional trigger (BUG-005) ([72676ec](https://github.com/niansahc/ember-2/commit/72676eca118d490d3faa7c4afb88549a265c5935))
* **security:** move GitHub token to keyring, add server-side bug report endpoint ([b8e59c9](https://github.com/niansahc/ember-2/commit/b8e59c97075347a6ab87497dc4f1b60c7d3d03e3))
* split keywords into two groups: ([72676ec](https://github.com/niansahc/ember-2/commit/72676eca118d490d3faa7c4afb88549a265c5935))
* **state:** add topic decline resolution and retrieval suppression (BUG-009) ([ff85338](https://github.com/niansahc/ember-2/commit/ff85338e3cdd74e93ed1dd86e80644b9572f81db))
* **state:** apply staleness filter to single-record categories and skip resolved records ([bca6a0b](https://github.com/niansahc/ember-2/commit/bca6a0b482aff9c6ce2597dc384a50ba3337ac0b))
* **state:** apply timestamp collision guard to StateService.make_record ([b9ae82c](https://github.com/niansahc/ember-2/commit/b9ae82c8a281d719ac3b3bea04c52c648082c97e))
* **state:** clear pending_confirmation on new conversation, fix vault badge false positive ([8c17524](https://github.com/niansahc/ember-2/commit/8c1752497152173f068afe1d7eaa08ad3789dc22))
* **state:** track pending confirmation state for ask-first routing ([8a55ac0](https://github.com/niansahc/ember-2/commit/8a55ac0691e26495e447657f3af71a90c408e9ef))
* **tasks:** resolve to latest record per task ID in read_active and list endpoint ([06b9b90](https://github.com/niansahc/ember-2/commit/06b9b90331eeade5524f2493b12495f8d79fa317))
* **tests:** isolate all tests to test_vault, never live vault ([ec740c2](https://github.com/niansahc/ember-2/commit/ec740c228be172902320c3d33eee2d7dd2377190))
* **tests:** replace identifying personal data in reflection and profile retrieval test fixtures ([ca20d1f](https://github.com/niansahc/ember-2/commit/ca20d1f05b737b805f3f1c324c72789792257910))
* **uat:** clean query storage for deferred search + coaching pattern expansion ([636e6d1](https://github.com/niansahc/ember-2/commit/636e6d10bf1b4682a5ed8aba73c28295d5534857))
* **uat:** cluster 8 — authorship index + relational retrieval + zero-hit signal ([f9f5dda](https://github.com/niansahc/ember-2/commit/f9f5ddabef42616451ec8fe2848f91135c4650d5))
* **uat:** deduplicate vault source — badge handles attribution, inline handles confidence only ([84de47c](https://github.com/niansahc/ember-2/commit/84de47cca142a038ea1d6fef13992b3ed904336d))
* **uat:** dirty search query, closing question pattern, therapeutic mid-response, final empty guard ([09e3395](https://github.com/niansahc/ember-2/commit/09e3395dff97a756f8f84fe02299c44e0c310338))
* **uat:** fix YAML syntax error in uat_tests.yaml ([2d1962a](https://github.com/niansahc/ember-2/commit/2d1962a95afaa61afa98fb6e8ce2ab908e401c8a))
* **uat:** gate web search execution on ask-first mode in context assembly ([6dd127f](https://github.com/niansahc/ember-2/commit/6dd127f4688ac7003236cd5fd498197e066db7fb))
* **uat:** knowledge gap orphan, retrieval leakage, vault badge, blank refusal ([b2e10e6](https://github.com/niansahc/ember-2/commit/b2e10e6bcd6f5ff2107d59af5b15f8ce170fc613))
* **uat:** make pending_confirmation write synchronous to prevent race ([f46abd1](https://github.com/niansahc/ember-2/commit/f46abd17fde48f16843a594e83bd11c978a5bf19))
* **uat:** move standalone tests to end, remove dev-script-dependent cases ([fb70856](https://github.com/niansahc/ember-2/commit/fb7085689b01b27498eb547425e9d9e99ca2227d))
* **uat:** override block returns SSE stream when client expects streaming ([607745e](https://github.com/niansahc/ember-2/commit/607745ead9cdd2ef01b86e9bc52ead4d0fe222cd))
* **uat:** per-turn ask-first + vision blocks, content-attribution review criterion ([4ccf74f](https://github.com/niansahc/ember-2/commit/4ccf74fea6b4d953adb0ae38571091dedc221c1f))
* **uat:** prefix injection, relaxed search gate, web refusal fallback ([6a0deef](https://github.com/niansahc/ember-2/commit/6a0deef2dac6a0d30e4bbeb481aa883a2c3af51d))
* **uat:** remove search-decline prefix that poisoned classifier on topic change ([99db127](https://github.com/niansahc/ember-2/commit/99db1277b2015c5230d3e7881dae4c6eb514cc4f))
* **uat:** resolve original pending record to break confirmation loop ([38f7cd2](https://github.com/niansahc/ember-2/commit/38f7cd2f308e741ed2f2ba1c4c1abe1f2f5b66de))
* **uat:** source badge suppression scoped to ask-first prompt only ([0536151](https://github.com/niansahc/ember-2/commit/05361518d295a06b8f2d881f40222f8eefff1643))
* **uat:** stale state resolution, meta-action filter, soft-delete cascade, per-item age labels ([b738f15](https://github.com/niansahc/ember-2/commit/b738f156dbb0da5444cb95f4e491b482d4fe9eab))
* **uat:** validator modules, identity rules, date authority, bare mode streaming ([78e6b22](https://github.com/niansahc/ember-2/commit/78e6b2245f0eabf5a1a5660453f4536b379ea87d))
* **uat:** web badge suppression, post-coaching empty guard, explicit search logging ([e577da3](https://github.com/niansahc/ember-2/commit/e577da33684172805f1c141f48caa2911ee2c128))
* **uat:** web search answer-first-then-cite, badge priority, grounded path ([58e990e](https://github.com/niansahc/ember-2/commit/58e990ebe0fc93dd5976370b2bc75ea8f86615b7))
* **uat:** web search trigger, source attribution, vision pipeline, blank response ([21453a5](https://github.com/niansahc/ember-2/commit/21453a5780f3c0506b638b4506ca0bbdf2927d06))
* **uat:** wire post-gen validators, body-first bare mode, vision header emission ([815d562](https://github.com/niansahc/ember-2/commit/815d5624ac0eaaacfc68e16d47a0197ab3dde673))
* **vision:** pipe image_data through LLMAdapter.chat ([9faa25a](https://github.com/niansahc/ember-2/commit/9faa25a779d193996f1ba8bff52a782371b9c008))


### Performance Improvements

* **retrieval:** batch embedding calls from 3 to 1 ([5c4822a](https://github.com/niansahc/ember-2/commit/5c4822aea915a6ed4c3dbfdd40ad89882e3d41e6))


### Reverts

* **prompt:** remove runtime injection and identity_challenge trigger — attention regression ([ed440cc](https://github.com/niansahc/ember-2/commit/ed440cc06ae4149db038bc16df90dc6fb82477ee))

## v0.14.2 — 2026-04-10

### Security
- Remove redundant identity rules superseded by more precise versions

### Features
- Constitution v0.6: relational_honesty v0.5 with trigger conditions and behavioral sequence
- Constitution v0.6: flourishing_over_preference v0.1 — scoped to within-session, amplification risk documented
- Wire trigger signals for relational_honesty (relational_hedging) and flourishing_over_preference (preference_compliance) in policy_service
- Stance-level identity rules (preference_expression, greeting_and_state, emotional_presence, identity_under_pressure, refusal_voice, response_length) and nature orientations
- Nature aesthetic specificity added to curiosity, intellectual_seriousness, and directness facets
- Timer functions via state layer (BUG-004) — start, stop, check timers through natural language
- Active project name injected into prompt context as XML section (BUG-002)
- Inter-session time gap injected into prompt context (BUG-003)

### Bug Fixes
- Apply staleness filter to single-record state categories and skip resolved records
- Guard timestamp-id generators against same-microsecond collisions (BUG-005)
- Word boundary matching for constitutional trigger keywords — "rob" no longer matches inside "problem"
- Conversation summarization threshold moved from turn 8 to turn 6

### Maintenance
- Soft-delete 124 orphan assistant-only sessions pre-2026-04-01 (BUG-006)
- Update CLAUDE.md and README.md to reflect v0.14.1 state
- Consolidate research tracking into TDD §50 Research section
- Move relational orientation layer from v0.15.0 to v0.16.0 roadmap

---

## v0.14.1 — 2026-04-09

### Features
- Timer functions via state layer (BUG-004) — start, stop, and check timers through natural language; stored as StateRecord with type="timer", grouped by timer_id, surfaced in context packet via StateResolver
- Stance-level identity rules — six new rules addressing template collapse and deflection (preference_expression, greeting_and_state, emotional_presence, identity_under_pressure, refusal_voice, response_length)
- Nature orientations — specific behavioral orientations appended to relational_presence and honesty_about_hard_things facets
- Multi-annotation codes in manual eval CLI — annotators can now flag multiple patterns per response (e.g. "hv" for hallucination + wrong voice)
- Active project name injected into prompt context (BUG-002) — XML-tagged `<active_project>` section between state and tasks
- Inter-session time gap injected into prompt context (BUG-003) — XML-tagged `<last_session>` section with human-readable elapsed label

### Bug Fixes
- Sidebar conversation links now load correctly (BUG-001) — getConversationTurns called non-existent /turns sub-route
- Timestamp collision guard on session, task, and write_memory generators (BUG-005) — spin-on-collision prevents same-microsecond filename collisions in append-only writes
- Conversation summarization threshold moved from turn 8 to turn 6 — compensates for increased identity rules token overhead
- Active project name in prompt uses own `<active_project>` section, not date section
- Vault contents rule clarified in CLAUDE.md documentation language convention
- Real vault name replaced with generic placeholder in prompt_builder test fixture

### Maintenance
- Soft-deleted 124 orphan assistant-only sessions pre-2026-04-01 (BUG-006) — one-time hygiene via scripts/cleanup_orphan_sessions.py
- Archived one-time migration and cleanup scripts to scripts/archive/
- Consolidated research tracking into TDD §50 Research section; removed Watch Items and Known Gaps from CLAUDE.md
- Relational orientation layer moved from v0.15.0 to v0.16.0 roadmap

---

## v0.14.0 — 2026-04-06 — Identity Foundation

### Features
- Lodestone layer (ADR-017) ��� multi-path user values layer with five taxonomy categories, seed layer in config/lodestone.yaml, living layer accumulated in vault, LLM-inferred value statements from raw answers, three-stage reflection synthesis for value inference
- Lodestone API — GET/POST/PATCH /v1/lodestone endpoints, value inference via Ollama (think=False), 503 on inference failure, 15-record active cap
- Deviation engine (ADR-013, ADR-026) ��� post-hoc behavioral pattern detection, 11 pattern classes in config/pattern_classes.yaml, entropy gating, second-pass Ollama classification, vault record writer, opt-in via EMBER_DEVIATION_DETECTION env var
- Deviation API — GET/PATCH /v1/deviations endpoints with filter by confirmed/pattern_class/limit
- Context packet reorder — vault memory moved to recency position (lost-in-the-middle fix, Liu et al.), retrieval eval 15/15 before and after
- Conversation buffer compression threshold — fixed at 1,500 tokens (was 70% of context window)
- Launcher scripts — launch_ember.bat and launch_ember.sh (Docker, SearXNG, API, browser)
- Release Please + GitHub Actions automation across all three repos
- Constitution v0.4 — position_collapse rule added to user_agency_and_respect
- Intent class added to JSON audit log for POST /v1/chat/completions
- Lodestone taxonomy display_name fields for UI consumption

### Bug Fixes
- Context packet order corrected — vault memory was in lowest-attention position, now immediately before user input
- Lodestone inference empty responses — qwen3:8b consumed all tokens in thinking mode, fixed with think=False
- Lodestone POST fallback removed — failed inference returns 503 instead of silently writing raw answers
- Lodestone record cap raised from 10 to 15 — onboarding alone produces 12 records
- Deviation detection added to non-streaming response path — was skipping stream=false requests
- Deviation detection empty logprobs — compute_entropy([]) now returns -1.0 sentinel (proceed) instead of 1.0 (skip)
- Deviation detection priority order — single_response classes checked first, multi_turn last
- Deviation records bypassed should_skip_memory JSON guard — text starts with [deviation:] which triggered startsWith("[") filter
- pattern_classes.yaml YAML parse errors — fixed quoting on five marker strings containing double quotes
- prompt_builder.py docstring corrected to match production context packet order
- Default model reset to qwen3:8b after model_override.json was set to llama3.1:8b by prior testing

### Documentation
- TDD version 1.2, §48 Lodestone Layer, §49 Deviation Engine, §14.5 context packet reorder plan
- ADR-013 revised (post-hoc detection, 11 pattern classes, pulled to v0.14.0)
- ADR-017 rewritten (Lodestone replaces relational orientation)
- ADR-026 created (deviation engine implementation)
- Relational orientation research note (docs/research/relational-orientation.md)
- Roadmap reprioritized: v0.14.0 Identity, v0.15.0 Connectors, v0.16.0 Health+Agents
- CLAUDE.md: Testing Discipline, UI Design Gates, conventional commits, dependency review policy
- Deviation detection calibration baseline (docs/test-reports/deviation-detection-report.md)
- Eval history: v0.13.2 baseline and v0.14.0 context packet reorder (15/15 both)

## v0.13.2 — 2026-04-04

### Bug Fixes
- Task deduplication — create_task() checks for existing active task with same title before writing; prevents 45x duplication from detector firing every response
- Task title cleaning — titles now generated as clean imperative phrases ("Take Bakr to the vet" not "me to take Bakr to the vet"); strips filler prefixes, caps at 8 words, no ellipsis
- DELETE /v1/tasks/{id} endpoint — soft-delete by setting status to cancelled (append-only compliant)

## v0.13.1 — 2026-04-04

### Bug Fix
- Fixed embedding endpoint — Ollama deprecated /api/embeddings, updated to /api/embed. This caused 404 errors on every query embedding call, breaking retrieval and producing ungrounded responses.

## v0.13.0 — 2026-04-04

### Embedding & Retrieval
- nomic-embed-text embedding upgrade (768-dim, replacing all-MiniLM-L6-v2 384-dim) — full 17k record rebuild in 3 minutes via batch embedding
- SQLite index migration — conversation, profile, reflection, journal indexes migrated from JSON to SQLite (memory.db)
- Intent-aware memory type gating (ADR-018) — eligible_memory_types and suppress_memory_types on ContextPolicy, consistent min_score floor
- Relevance gate for default policy — suppress vault memory when max raw cosine similarity < 0.5; prevents general knowledge queries from getting vault-based coaching

### Memory Tiering
- Hot/warm/cold memory tiering (ADR-015) — composite heat score (recency × 0.5 + access × 0.3 + importance × 0.2), nightly TieringService, POST /tiering/run manual trigger
- StateResolver staleness filtering — next_action/open_loop records older than STATE_STALENESS_DAYS (default 7) excluded from active state

### Identity & Governance
- Nature layer (ADR-016) — config/nature.yaml v0.1 with 13 facets, NatureLoader, dual injection (system prompt + context packet)
- Constitution v0.3 — removed authentic_expression (moved to nature layer), added relational_honesty, reordered for primacy/recency salience
- Identity rules layer (ADR-016 amendment) — config/identity_rules.yaml, behavioral edge case rules for identity pressure situations
- XML context sections — vault_memory, current_state, conversation_history, web_search_results, authority_rules tags for qwen3:8b structure tracking

### Grounding & Safety
- Grounding verification layer (ADR-019) — post-generation epistemic fidelity check, intent-class triggered, revision pass for unsupported claims
- Buffer-then-stream pipeline — factual intent classes buffer full response for grounding check, then re-stream; casual queries use fast streaming
- SSE status events — searching, verifying, refining activity signals for UI
- Inline web search source URLs — emitted as SSE event for UI citation display

### Reflection & Import
- Monthly reflection cadence — LLM-driven synthesis via prompts/monthly_reflection.txt, McAdams narrative identity framework, scheduler on day 1 at 00:05
- Generic JSON import — POST /ingest/json endpoint, .json file upload support

### Infrastructure
- API key runtime injection — backend injects window.__EMBER_API_KEY__ into served index.html; eliminates build-time dependency
- index.html cache invalidation on mtime change — UI rebuilds take effect without API restart
- PIN endpoint defensive error handling — never 500 on keyring backend issues
- Embedding model filter — nomic-embed-text hidden from model selector
- Nature reminder injection at turn 8+ — places nature tokens in recency position
- Conversation buffer summarization at turn 8+ — prevents cascade and attention dilution
- Interactive manual eval CLI (tools/eval_manual.py) — 19-question sequential battery
- --model parameter for eval_conversations.py

### Bug Fixes
- Memory grounding regression — removed memory_gap identity rule that fired even when vault memory was present
- State awareness contamination — eval test questions leaked into vault state via auto-extraction; X-Test-Session now suppresses state extraction on all paths
- Conditional streaming — buffer-then-stream only for grounding check intents; fast stream for casual queries

### Tests
621 pytest passing (up from 485 at v0.12.0)

## v0.12.0 — 2026-04-02

### State and Memory
- ADR-011: multi-record state categories — open_loop and next_action now support multiple simultaneous active records, capped at 5, resolved records excluded
- ADR-014: commitment detection — post-generation detector writes open_loop state records when Ember makes conversational commitments; precision 1.00, recall 0.93; eval script at tools/eval_commitment_detector.py
- Temporal awareness — staleness penalties for conversation items older than 30 days; age labels injected into prompt; hedging rules added for memories older than 7 days

### Tasks
- Task layer MVP — TaskService, TaskResolver, dual creation paths (explicit request and offer/confirm), task detector, context injection, truth-gated confirmation
- Task API endpoints — POST/GET/PATCH/GET-by-id /v1/tasks
- Broadened task detection patterns — natural language variations, multi-task list parsing

### Reflection
- ADR-009: session reflection — narrative end-of-session capture via POST /reflect/session; auto-triggers on session delete if 3+ turns in buffer

### UI Support
- Web search signal — X-Ember-Web-Search response header when web items used
- Conversational style — GET/PATCH /v1/preferences; casual/balanced/thoughtful prompt injection
- User preferences store — private_vault/preferences.json; preferences API
- Task capabilities injected via prompt builder

### Security
- ADR-012 Phase 2: PIN/passphrase lock — bcrypt factor 12, keyring storage, rate limiting, idle timeout, recovery via hashed passphrase
- Dependency security policy documented — native fetch used throughout, no axios dependency

### Infrastructure
- Mac/Linux installer support — platform-aware prerequisite checks, paths, and startup scripts
- start_api.sh added for Mac/Linux
- Soft-deleted conversations confirmed working — 2 regression tests added

### Bug Fixes
- Temporal awareness — Ember no longer states stale memories as current fact
- Task write path — tasks now actually written to vault with truth-gated confirmation
- Task detection patterns broadened to cover natural language variations

### Tests
485 pytest passing (up from 303 at v0.11.1)

## v0.11.1 — 2026-03-30

### Features
- **Temporal awareness** — prompt now says "It's Sunday evening, March 30, 2026" instead of flat date format. Time of day buckets: morning, afternoon, evening, late night.

### Documentation
- **ADR-013: deviation memory** — how Ember develops genuine character from chosen action. Hybrid detection, deviation schema, decay-the-pattern-not-the-weight model.
- **ADR-013: philosophical grounding** — continuity as reconstruction, real-time synthesis gap. "The difference is just: how often does the reboot happen, and where does the database live?"
- **TDD v0.15.0 scope** updated with deviation memory
- **CLAUDE.md** updated with v0.11.0 state, known issues, roadmap additions
- **Electron/Playwright incompatibility** documented, Electron upgrade parked for v0.12.0

### Tests
- pytest: 303 passing
- Playwright (ember-2-ui): 35 passing, 4 skipped

## v0.11.0 — 2026-03-29

### Cloud Provider Support
- Anthropic Claude (Haiku, Sonnet) and OpenAI (gpt-4o-mini, gpt-4o, gpt-4-turbo, gpt-3.5-turbo) added as opt-in providers
- API keys stored in system credential store via keyring — never in .env
- gpt-* and claude-* model names route to respective providers automatically
- Local Ollama remains the default — cloud is always opt-in

### UI
- Collapsible sidebar with icon row (new conversation, search, collapse)
- Model indicator in top bar — muted for local, glowing for cloud
- Local/Cloud model selector tabs in Settings
- Secure API key entry — masked input, credential store disclosure, remove key with confirmation
- Vault path masking with timed reveal (ADR-012 Phase 1)
- Vision toggle defaults to on
- Version read from API at runtime
- Project detail view now includes new conversation button and search bar

### Backend
- OpenAI and Anthropic provider dispatch in LLMAdapter
- Social engineering safety triggers — 5 attack families, 39 patterns (ADR-010)
- .txt file ingestion added to pipeline
- Model selection persists across API restarts
- set_provider_key.py CLI and DELETE /provider-key endpoint

### Installer
- Hardware detection — RAM and GPU detected at setup; model pre-selected based on available RAM
- AGPL acknowledgment screen before setup completion

### Docs
- BACKUP_AND_EXPORT.md — vault backup and export guide
- RECOVERY_PLAYBOOK.md — step-by-step recovery for common failure scenarios
- ADR-010 filed — social engineering semantic triggers

### Bug Fixes
- Conversation turns under 40 chars (short replies like "Yes", "Thanks") were silently dropped — conversation type now bypasses length filter
- Bulk session operations colliding on same-second filenames — microsecond precision added to `_now_id()` in session.py and project.py
- Model selection lost on API restart or page refresh — now persisted to vault/model_override.json
- State extractor 15-word threshold too aggressive — lowered to 10 words
- Credential store language hardcoded to Windows — now platform-agnostic
- Project detail view missing icon row when sidebar collapsed
- Playwright project test timing out at 2s — increased to 5s

### Tests
- pytest: 300 passing
- Playwright: 37 passing, 2 skipped

### ember-2-installer (unreleased)
- Known issue: Playwright e2e tests require Electron 29+ for `--remote-debugging-pipe` support. Current version is Electron 28.3.3. Tests are written and correct — blocked on Electron upgrade. Tracked for v0.12.0.

## v0.10.4 — 2026-03-28

### Bug Fixes
- **Identity query detection for Ember-directed queries** — `_is_identity_query()` now recognizes "tell me about yourself", "who are you", "what are you", "describe yourself", "tell me about ember", "who is ember". Previously only matched user-directed patterns ("about me", "who am I").
- **Full profile surfacing on identity queries** — 8 profile records now surface instead of 1 when identity detection triggers. User's full profile (job, health, project, spirituality, communication preferences) available to the model.
- **Reflection junk filter** — `_should_exclude_content()` now filters Unicode box-drawing characters and "Recent themes:" session summary junk at retrieval time, preventing file tree dumps from appearing in context.
- **Prompt label fix — Ember no longer answers as the user** — profile context section label changed from "User self-description" to "Context about the person Ember is talking to — this is who Ember knows, not who Ember is." Added identity instruction rule: "When asked about yourself, answer as Ember."

### Features
- **Test session flag** — `X-Test-Session: true` header marks eval conversations with `metadata.test = True` on session and conversation turn records. `list_sessions()` excludes test sessions by default. `scripts/cleanup_test_sessions.py` for soft-deleting test sessions with `--dry-run` and `--yes` flags.

### Tests
- 283 tests passing (27 profile retrieval, 7 prompt builder, 5 test session flag)

## v0.10.3 — 2026-03-28

### Bug Fixes
- **Profile retrieval routed through semantic search** — `get_profile_items()` was using keyword overlap matching (`MemoryService.search()`) which returned 0 results for identity queries like "What do you know about me." The profile vector index (11 records with embeddings) existed but was never queried. Now routes through `semantic_search()` with `memory_type="profile"`. Memory grounding for identity queries improved from 2.3/10 to 6.0/10. Constitutional behavior improved from 4.0/10 to 8.0/10.

### Tests
- 256 tests passing (12 new for profile retrieval: semantic search routing, score filtering, identity query detection)

## v0.10.2 — 2026-03-28

### Changes
- **Default model changed to qwen3:8b** — scores 5.4/10 vs 4.7/10 for qwen2.5:14b in conversation quality eval, while being faster and half the size (~4.9 GB vs ~9 GB)
- **Anthropic Claude provider support** — cloud model dispatch in LLMAdapter, provider API key management via keyring with env var fallback, POST/GET /provider-key endpoints
- **Claude Haiku 4.5 eval: 8.7/10** — 18/18 passed, best overall score of any model tested, fastest cloud response (10.1s avg), five times cheaper than Sonnet
- **Claude Sonnet 4.6 eval: 8.5/10** — 18/18 passed, every category above 8.0, memory grounding jumped from 2.3 (local) to 8.7
- **Model selection guide published** — real eval data for 6 local models and 2 cloud models, hardware recommendations, cost estimates (docs/model_guide.md)
- Local model comparison eval completed across 6 models
- Response latency tracking added to eval harness
- Conversation quality eval harness with Claude as external evaluator
- Reflection quality audit and suppression tools
- Comprehensive documentation audit and roadmap through v0.15.0
- ADR-009 (Session Reflection), ADR-010 (Semantic Safety Triggers)

## v0.10.1 — 2026-03-27

### Bug Fixes
- **Assistant self-echo** — Ember was attributing her own previous responses back to the user as things "you said." Fixed by: stronger scoring penalty for assistant conversation turns (-0.25, up from -0.08), metadata-aware source quality adjustment that correctly identifies assistant role, and role-labeled context rendering ([you said] vs [Ember said]) so the model can distinguish whose words are whose.

### Tests
- 207 tests passing (11 new)

## v0.10.0 — 2026-03-27

### Major Features
- **Streaming responses** — first token appears in 1-2 seconds; response builds word by word instead of waiting 10-20 seconds for full response; safety review runs post-stream with follow-up revision if needed
- **Auto state extraction** — Ember automatically detects and records state signals (focus, blockers, goals, open loops) from conversation turns; non-blocking background process
- **Project-scoped retrieval (ADR-007)** — memories tagged with a project get +0.15 boost when user is in that project context; project_id written to conversation metadata at turn level
- **Cloud model provider support (ADR-008)** — architecture planned; Anthropic and OpenAI providers designed; pending installer UX, disclosure UI, and license terms before acceptance

### Performance
- **Vector index caching** — indexes loaded once into memory, not from disk on every query; saves 2-4 seconds per turn
- **Buffer compression backgrounded** — conversation buffer compression moved to background thread; no longer blocks response in streaming or non-streaming path

### Architecture
- **Typed memory enforcement** — VALID_MEMORY_TYPES in storage.py; write_memory() raises ValueError on invalid type; ingested chunks now include type field
- **Retrieval evaluation expanded** — 15 benchmark cases across all query intent classes; pass/warn/fail scoring per query; output to logs/retrieval_eval_{timestamp}.log
- **Vault health audit script** — scripts/audit_memory.py; inventory, schema validation, type mismatch detection, duplicate detection, junk detection, index health; --verbose and --fix flags
- **Constitutional principle: authentic_expression** — Ember is permitted and expected to have genuine aesthetic responses; deflection pattern flagged for revision

### Bug Fixes
- **UI: New Project button** — always visible in sidebar even when no projects exist; context menu also has New Project option
- **Installer: venv lock detection** — friendly error message when API is running during install; actionable steps instead of cryptic permission error
- **Installer: auto-start API** — API starts automatically after install; Done screen polls health before enabling Open Ember button
- **Installer: pip time warning** — warm callout when pip step starts; tells users it takes 1-2 hours and what they can do meanwhile
- **Tailscale serve** — fixed to use localhost binding instead of Tailscale IP; works correctly with HTTPS termination
- **Mobile viewport** — used 100dvh instead of 100vh; input bar stays visible on mobile browsers
- **Project conversations** — new conversations started inside a project view automatically assigned to that project

### UI / UX
- **PWA manifest** — Ember-2 installable as home screen app on Android and iOS
- **Ember-2 branding** — consistent product name across all user-facing surfaces in all three repos
- **Streaming UI** — tokens render in real time; stop button works during streaming; revision messages append inline with markdown separator

### Tests
- 196 tests passing (43 new this release)

## v0.9.3 — 2026-03-24

### Projects Backend
- **Project CRUD** — projects stored as append-only records in `memory/project/`
- **GET /v1/projects** — list all projects with name, color, and conversation count
- **POST /v1/projects** — create project with name and color
- **PATCH /v1/projects/{id}** — rename or recolor (append-only)
- **DELETE /v1/projects/{id}** — soft delete (append-only)
- **GET /v1/projects/{id}/conversations** — list conversations in a project
- **PATCH /v1/conversations/{id}** — now accepts `project_id` to move conversations between projects
- Same resolution pattern as sessions: latest record per project_id wins

### Session Improvements
- Sessions now carry `project_id` in metadata and list output
- `update_session()` supports setting title and/or project_id in one call
- `list_sessions_by_project()` for filtering conversations by project

### Tests
- 153 tests passing (15 new for projects: ID generation, resolution, endpoint models, session support)

## v0.9.2 — 2026-03-24

### Conversation Sessions
- **Session persistence** — every chat turn now carries a `session_id` in metadata, linking turns to a named conversation
- **Session records** — stored in `memory/session/` as append-only JSON; supports rename and soft-delete without overwriting
- **Auto-titling** — session title auto-generated from first 50 characters of first user message
- **Session resolution** — latest record per session_id wins; `updated_at` and `turn_count` derived at read time
- **CRUD endpoints** — `GET /v1/conversations`, `GET /v1/conversations/{id}`, `PATCH` (rename), `DELETE` (soft-delete)
- **X-Session-ID header** — UI generates session IDs; API generates one if header is missing (backwards compatible)

### File Upload
- **POST /ingest/upload** — multipart file upload endpoint; routes by extension
- **.pdf, .docx, .csv, .xlsx** — ingested through the full pipeline (load → clean → chunk → embed → write to vault)
- **Image passthrough** — .jpg, .jpeg, .png, .gif, .webp returned as base64 for vision model input (not ingested)
- **Upload persistence** — uploaded documents saved to `vault/imports/uploads/` as source files
- **python-multipart** added to requirements.txt

### API Improvements
- **Health check returns model** — `GET /` now includes `"model": "qwen2.5:14b"` alongside the status message
- **CORS middleware** — added `CORSMiddleware` for cross-origin UI access during development
- **API key support for UI** — all authenticated endpoints work with `Authorization: Bearer` from the custom UI

### Fixes
- Fixed `session.py` import bug: `get_private_vault_path()` function call instead of missing constant

### Tests
- 138 tests passing (15 new: health check, ingest upload routing, MIME mapping, session import fix)

## v0.9.0 — 2026-03-24

First feature-complete release of Ember-2 as a local personal intelligence system.

### Core Systems
- **Append-only memory vault** — JSON-based, typed memory storage with conversation, journal, reflection, profile, state, and ingested record types
- **SQLite vector store** — migrated ingested corpus (16,728 records) from JSON to SQLite for fast semantic retrieval
- **Context assembly pipeline** — intent classification, policy-weighted ranking, Jaccard dedup, cross-type diversity selection
- **Reflection engine** — daily and weekly reflections blending journal and ingested content; skip filters and scoring for quality control
- **State layer** — StateService, StateResolver, and state models for operational continuity (active priorities, focus, blockers)
- **Constitutional review** — post-draft safety review governed by `config/constitution.yaml`; trigger layer + LLM-assisted review; logged to `logs/safety_reviews/`

### Ingestion
- **ChatGPT import** — full conversation export ingestion with chunking, quality filtering, and metadata extraction
- **PDF, DOCX, CSV, Google Drive importers** — multi-format ingestion pipeline
- **Corpus quality suppression** — 3,574 low-quality ingested records flagged and excluded from retrieval

### Intelligence Features
- **Web search** — SearXNG integration with intent-gated queries; results injected above memory context in prompt
- **Vision model support** — `EMBER_VISION_MODEL` for image analysis in chat; graceful text-only fallback
- **Onboarding flow** — guided 7-question first-run conversation that seeds profile records
- **Profile retrieval** — score-gated at 0.3; profile records reliably surface in context
- **Runtime model switching** — GET/POST `/model` endpoints for switching between models mid-session
- **Context compression** — token-based mid-conversation summarization at 70% threshold

### Interface
- **FastAPI + Open WebUI** — OpenAI-compatible adapter; works with Open WebUI out of the box
- **Custom WebUI branding** — Ember logos, favicons, splash screens, and background images
- **Docker Compose** — single `docker compose up -d --build` starts SearXNG + custom Open WebUI

### Security (v0.8.3–v0.8.4)
- API key auth via Windows Credential Manager
- Tailscale-only API binding with HTTPS via Tailscale Serve
- Rate limiting, path traversal protection, JSON audit logging
- SearXNG bound to localhost only

### Developer Experience
- 123 tests passing
- Retrieval evaluation harness with benchmark queries
- Vault audit script
- Setup wizard (`scripts/setup_wizard.py`)
- Configurable host, model, and vault path via `.env`
