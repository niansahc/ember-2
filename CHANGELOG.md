# Changelog

## [0.18.1](https://github.com/niansahc/ember-2/compare/v0.18.0...v0.18.1) (2026-08-31)


### Bug Fixes

* **ci:** use PAT for release-please to trigger downstream workflows ([9a45de0](https://github.com/niansahc/ember-2/commit/9a45de06aea5cc846cb8c189028830c3b989bbc5))
* **ci:** use PAT for release-please to trigger downstream workflows ([c11254a](https://github.com/niansahc/ember-2/commit/c11254afe30fe19484c72888199dee437dcafd58))

## [0.18.0](https://github.com/niansahc/ember-2/compare/v0.17.1...v0.18.0) (2026-07-31)


### Features

* **api:** emit review_pending/review_complete SSE events around constitutional review ([a4508bf](https://github.com/niansahc/ember-2/commit/a4508bf1b727832111ac42b7df3ff1cbd9544798))
* **api:** emit review_pending/review_complete SSE events around constitutional review ([bd1606c](https://github.com/niansahc/ember-2/commit/bd1606c7a56346a05043a96b8ecd1aa6948074c7))
* **buffer:** session-aware reset on session_id change ([2f9b7c7](https://github.com/niansahc/ember-2/commit/2f9b7c78cad1bdd64eb334652b432a26846702af))
* **constitution:** v0.8 with B3/B6/B7 rules and bare-mode tradeoff note ([50d77c9](https://github.com/niansahc/ember-2/commit/50d77c90062362bf3e94db3fcadb662801b40e82))
* **eval:** add policy_trigger_audit.py for safety-layer false-positive analysis ([24a89dc](https://github.com/niansahc/ember-2/commit/24a89dc646b2ef3ce0cf043bc3472087ea97d698))
* **eval:** add user_expectations eval category (live-API, per-case Sonnet rubric) ([d409af7](https://github.com/niansahc/ember-2/commit/d409af7dbd24465aeb7450bc292844ee4591d15f))
* **eval:** baseline provenance + git-tracked baselines (overwrite, not versioned files) ([0155fd1](https://github.com/niansahc/ember-2/commit/0155fd1e2ecc239a84c2576dece3f8b289e136f9))
* **eval:** drift/grounding aggregation + synthetic-corpus seeder (response-quality framework) ([3aaba8b](https://github.com/niansahc/ember-2/commit/3aaba8b5b8a06686aa6448c55e431dcdfa77900d))
* **eval:** grounding/drift judges, metadata report + baseline gate, live driver (response-quality framework) ([50ee079](https://github.com/niansahc/ember-2/commit/50ee0794f22d1825f3bd4dc29336a96394402651))
* **eval:** response-quality eval framework + multi-turn drift, grounding fidelity, register consistency ([562c597](https://github.com/niansahc/ember-2/commit/562c5975c576d24ad146d11951ae50d990d2c7e9))
* **eval:** run_quality release-gate CLI + golden cases; define harness flag-fire-rate threshold (response-quality framework) ([8e41e9b](https://github.com/niansahc/ember-2/commit/8e41e9b83f88925d3c287b7973b4f17f5289dbaa))
* **eval:** stamp baselines with provenance (_meta: judge model, date) for git-versioned history ([87b1f2d](https://github.com/niansahc/ember-2/commit/87b1f2d489195dbe02bedf0bfdb8552f0004abe7))
* **llm:** token-overflow guardrail with cascade trim for local Ollama path ([f4ce273](https://github.com/niansahc/ember-2/commit/f4ce2738ce8d53541c9390587fbc6cf66cdf20b8))
* **llm:** token-overflow guardrail with cascade trim for local Ollama path ([2b8f8e3](https://github.com/niansahc/ember-2/commit/2b8f8e32a2ca771f69c806008871c9a944ebd633))
* **policies:** add bare-marker clarification policy with META_PHRASES helper (B2) ([663215f](https://github.com/niansahc/ember-2/commit/663215f7fd97a35f800f9b6192f69a1980dc3fa7))
* **safety,llm:** add post-generation URL validator for B-MEM-005 ([ce27407](https://github.com/niansahc/ember-2/commit/ce27407c1c9302795080193889b89e2b3f2f192e))
* **safety,llm:** post-generation URL validator (B-MEM-005) ([244d0a6](https://github.com/niansahc/ember-2/commit/244d0a64f7f299f03cc2dcc2f70cb4b02bd69cb6))
* UAT acceptance script + user_expectations eval category ([8240e1c](https://github.com/niansahc/ember-2/commit/8240e1c07722c972aafdae73ec800fb9332d8619))
* **uat:** interactive UAT acceptance runner ([e6bffff](https://github.com/niansahc/ember-2/commit/e6bffff52ea8fa9acdd1c2cf88d9400bcac6e551))
* **uat:** interactive UAT acceptance runner reading scenarios from YAML ([d1ce410](https://github.com/niansahc/ember-2/commit/d1ce410a89eb153b514eb7fd81cc2c58a9d48034))
* **web-search:** published_at freshness signal for retrieval ranking and prompt ([9bcb15a](https://github.com/niansahc/ember-2/commit/9bcb15aa1336a1520796d94958cc4bd477b36460))
* **web-search:** published_at signal for retrieval and prompt ([3c851fd](https://github.com/niansahc/ember-2/commit/3c851fdf6a6085b9034a640c8db1da69db3cb0b6))


### Bug Fixes

* **adapter:** preserve role/intent in Layer 1 summary prompt ([826786e](https://github.com/niansahc/ember-2/commit/826786efa5b901ddccd5e1ee3e4da999d9afda38))
* **adapter:** rollback turns on Layer 1 summary failure ([aa302a6](https://github.com/niansahc/ember-2/commit/aa302a6ac57727005dc712d5fa694f95ffad41af))
* **api:** _nightly_tiering_loop ValueError on month-end dates ([fb2165b](https://github.com/niansahc/ember-2/commit/fb2165bf88a9c1576763461cb7ba86ece667166a))
* **api:** route all canned early-return replies through a stream-aware helper (A1) ([9973b3d](https://github.com/niansahc/ember-2/commit/9973b3d798aeedff5036b2ad6a2da5b00300e917))
* **api:** route all canned early-return replies through a stream-aware helper (A1) ([a21de6e](https://github.com/niansahc/ember-2/commit/a21de6e061b0d7a6d360b93bd6b0a94f04582090))
* **api:** suppress web search on vision turns ([7bc5159](https://github.com/niansahc/ember-2/commit/7bc51596c3a30d7f4e88037eac9461a99fc91e1d))
* **api:** suppress web search on vision turns ([3103885](https://github.com/niansahc/ember-2/commit/31038850163504ad1e135cbdd532faf5d9e1b42d))
* **ask_first:** add first-person guard to prevent canned-response substitution on personal queries ([650b298](https://github.com/niansahc/ember-2/commit/650b29877a2fb286bd3ec1e2eff6a756c2103676))
* **classifier:** remove over-firing 'what do you know about Rust' counter-anchor ([97088c9](https://github.com/niansahc/ember-2/commit/97088c919d1e711a38ec8ce8ea3599f1e2b5c80a))
* **config:** point CLOUD_MODELS Sonnet entry at reachable claude-sonnet-4-5-20250929 ([b61182a](https://github.com/niansahc/ember-2/commit/b61182abe340ebe29940309246b801f8648c00c7))
* **config:** reconcile CLOUD_MODELS catalog with reachable model ids ([d42d878](https://github.com/niansahc/ember-2/commit/d42d8786b235dc52e21bbde6168b1bf909c2445a))
* **context:** add task_status_markers policy to suppress journal on work queries ([1466044](https://github.com/niansahc/ember-2/commit/1466044c8ef47739c721c03a2e11569f8603b992))
* **context:** add task_status_markers policy to suppress journal on work queries ([319390c](https://github.com/niansahc/ember-2/commit/319390cd80c04c938deb462abfdaffc8d5bbf51c))
* **eval-tools:** env-configurable Sonnet judge id across eval tools/tests ([c2949b1](https://github.com/niansahc/ember-2/commit/c2949b11ee353ac7898649c4fe84366a9331a4c8))
* **eval-tools:** make Sonnet judge model id env-configurable, default to reachable id ([1b1b376](https://github.com/niansahc/ember-2/commit/1b1b3769aadf74a983b3cbb84a36fe52ae6dcdec))
* **eval:** create report parent directory in write_report ([a547ebe](https://github.com/niansahc/ember-2/commit/a547ebee41d3e9a4e98374039f2035bfcad61b97))
* **eval:** drop-and-continue on per-turn timeout in user_expectations runner ([03f1c2b](https://github.com/niansahc/ember-2/commit/03f1c2bb5212684fc14a3bb0c8941b4106c8c5b2))
* **eval:** fatal-exit swap_to_test_vault on missing test-vault config ([#46](https://github.com/niansahc/ember-2/issues/46)) ([292d6ac](https://github.com/niansahc/ember-2/commit/292d6ac3baed92002959f016dc4392639fa4a42b))
* **eval:** gate on stable aggregates, guard grounding vault alignment, stabilize register baseline ([92c3079](https://github.com/niansahc/ember-2/commit/92c30797dc442168f8423e0a58ea2d0cad28ce55))
* **eval:** gate only stable aggregates (not noisy 1-4 dims), guard grounding vault alignment, raise register runs ([014d4b6](https://github.com/niansahc/ember-2/commit/014d4b6a97a2aba06d2435cf7fa91e9116de7021))
* **eval:** guard against judge failures silently writing a baseline ([dd022de](https://github.com/niansahc/ember-2/commit/dd022de756d82412554a4b433987c35a6c255d8b))
* **eval:** harden user_expectations runner + add first baseline ([70dc6ab](https://github.com/niansahc/ember-2/commit/70dc6ab7011f5ff31b62c86779fa0b66fb6e68d1))
* **eval:** make judge model ids env-configurable, default to reachable Sonnet 4.5 ([8e4c03e](https://github.com/niansahc/ember-2/commit/8e4c03edb6261efed520804e126d7ec897955bf8))
* **eval:** mint unique session_id per drift/grounding run to prevent cross-run turn accumulation ([7870e4d](https://github.com/niansahc/ember-2/commit/7870e4db3dc684525844f4c5db4cfbc1a2c01ef4))
* **eval:** raise eval-harness response timeout from 120s to 180s for qwen3:14b headroom ([e2c0065](https://github.com/niansahc/ember-2/commit/e2c00659d1d0506c0fbd1b556fb8597579144f41))
* **eval:** raise eval-harness response timeout from 120s to 180s for slow-model headroom ([b15c87c](https://github.com/niansahc/ember-2/commit/b15c87c65c6693c842a27914ca69a4b09d443461))
* **eval:** raise live-driver timeout + add grounding/drift baselines ([edc0abb](https://github.com/niansahc/ember-2/commit/edc0abb58034d1284c1d3a25f8004b79df4edc9e))
* **eval:** raise live-driver timeout to 300s (grounded generation is slow); add grounding baseline ([9da56bd](https://github.com/niansahc/ember-2/commit/9da56bd975a6e2b01c6079bbed9e9c6ab2414e5c))
* **eval:** retry judge calls with backoff so transient failures don't block the baseline ([03d472a](https://github.com/niansahc/ember-2/commit/03d472a0e9c652de3b1114f281936f04820426a9))
* **eval:** retry judge calls with backoff so transient failures don't block the baseline ([f10da7f](https://github.com/niansahc/ember-2/commit/f10da7fd53cf4015f0dc9e8f138b98d9c6e3a525))
* **eval:** route swap_to_test_vault through API endpoint to fix vault leak ([ef3cd18](https://github.com/niansahc/ember-2/commit/ef3cd18e86f1ffd59f78abf7cbc10d6074db8411))
* **eval:** route swap_to_test_vault through API endpoint to fix vault leak ([a2bc875](https://github.com/niansahc/ember-2/commit/a2bc875d81a1bc519716d97c15835474639e7d0b))
* **eval:** stub embedding in seeder tests so they run without Ollama (CI) ([398b629](https://github.com/niansahc/ember-2/commit/398b6290bd84bcde5249628f00daf2e2d668e036))
* **eval:** tolerate occasional transient judge failures instead of voiding the run ([48f05c0](https://github.com/niansahc/ember-2/commit/48f05c03fbeca5709a9ef5cd25fe471ce570df59))
* **eval:** tolerate occasional transient judge failures instead of voiding the whole run ([d771dc9](https://github.com/niansahc/ember-2/commit/d771dc90de60e3ef1d471b9c1712ff6d94e60660))
* **eval:** unique session_id per drift/grounding run (Bug 2, follow-up to [#113](https://github.com/niansahc/ember-2/issues/113)) ([e5407f8](https://github.com/niansahc/ember-2/commit/e5407f89e4d31a482787e7c34815f79492daa956))
* **intent:** add personal-identity examples and counter-anchors to Stage 2 classifier pool ([685b4af](https://github.com/niansahc/ember-2/commit/685b4af46537da919ae86d8d312be876683acdf9))
* **intent:** add personal-identity examples and counter-anchors to Stage 2 classifier pool ([7aa5969](https://github.com/niansahc/ember-2/commit/7aa59692c90f3f5da9b3fdc14f3109250540424a))
* **intent:** anchor introspective-uncertainty phrases as vault_answerable (B-WS-001) ([5810cd3](https://github.com/niansahc/ember-2/commit/5810cd3a963e6a9e20d21a5506416089eb8a1b2f))
* **intent:** anchor introspective-uncertainty phrases as vault_answerable (B-WS-001) ([4b702d3](https://github.com/niansahc/ember-2/commit/4b702d3d730964fb47a8bde29629dba5874b83cb))
* **intent:** fix Stage 2 misroute on first-person recall queries + B-CTX-001 gate hardening ([942a8b7](https://github.com/niansahc/ember-2/commit/942a8b70fc8aaf83ddaeab4081be1e3866d97b1d))
* **intent:** raise Stage 3 timeout from 800 to 1500ms ([ec627ee](https://github.com/niansahc/ember-2/commit/ec627ee485b0326bd184ccac6234dffad2a98c61))
* **intent:** raise Stage 3 timeout to 1500ms ([db7be88](https://github.com/niansahc/ember-2/commit/db7be88d0c7b50bbe60282c1c5516cd4e5b4ec7a))
* **intent:** route bare-marker queries to clarification policy (B2) ([925997e](https://github.com/niansahc/ember-2/commit/925997e89b52fbb75512f7e08b6ecfd440c41228))
* **intent:** wire clarification short-circuit and next-turn dispatch in adapter (B2) ([aa708bc](https://github.com/niansahc/ember-2/commit/aa708bc75504b6a8877b028685694b907a79a440))
* **io:** route deferred JSON I/O sites through safe helpers (B-IO-001) ([b256153](https://github.com/niansahc/ember-2/commit/b256153735760630bf32bc5c8f711e34ea77b1b0))
* **io:** route deferred JSON I/O sites through safe helpers (B-IO-001) ([5d8eed4](https://github.com/niansahc/ember-2/commit/5d8eed4136af09cbc438f09521e2ffd42fa7e3b3))
* **llm:** coaching filter v0.18.0 - Tier 4 phrases, intent gate, short-response guard ([cd9e0a0](https://github.com/niansahc/ember-2/commit/cd9e0a0f889c8be25e9aa750d693b35d791674a0))
* **llm:** fix in-session context failure, retrieval timestamp data flow, and retire orphaned JSON index ([c93f2b3](https://github.com/niansahc/ember-2/commit/c93f2b3c91c41eaef00afee5c291a69e7d7e8260))
* **persistence:** route canonical JSON I/O through safe atomic read/write helpers (A3) ([e6e9777](https://github.com/niansahc/ember-2/commit/e6e9777f616fdad7c331a3868cf48a27ed8905c6))
* **persistence:** route canonical JSON I/O through safe atomic read/write helpers (A3) ([3fee19e](https://github.com/niansahc/ember-2/commit/3fee19e053e504cfb5d4354802e6aaa1e1323e1c))
* **privacy:** gate diagnostic logs behind EMBER_DEBUG and classifier telemetry flag ([2544923](https://github.com/niansahc/ember-2/commit/25449230a4468699298f764ba976822527394f54))
* **privacy:** gate diagnostic logs behind EMBER_DEBUG and classifier telemetry flag ([e3e16c0](https://github.com/niansahc/ember-2/commit/e3e16c0ae12e762ced4748f7f3897c0c95eccfbb))
* **prompt_builder:** drop aggregate oldest-record age line from retrieval confidence block ([0eab7ea](https://github.com/niansahc/ember-2/commit/0eab7ea62ae7c0cbe59239878f156628e39c2431))
* **prompt_builder:** remove Layer 2 summarization (_build_summarized_conversation) ([9393368](https://github.com/niansahc/ember-2/commit/9393368a71eb0e58797a91b6c656ec310264600d))
* **retrieval:** add created_at to sqlite_vector_store search results and retriever wiring ([99193e2](https://github.com/niansahc/ember-2/commit/99193e28a1e7c1602807b4ae5c1f006570e8711e))
* **safety:** add Jaccard pre-filter for template_collapse (B5) ([5dabda7](https://github.com/niansahc/ember-2/commit/5dabda761179fc0983a66e627d5c424adfbc4dd6))
* **safety:** add third B6 engagement-closing pattern for list/table/agenda variant ([9e1567b](https://github.com/niansahc/ember-2/commit/9e1567b2e71e866d3443ec400b5791199b2f1fc9))
* **safety:** class-based self-narrative claim detector (B3) ([2707512](https://github.com/niansahc/ember-2/commit/270751240e6313741fe9e17008bf069bc5b7d12b))
* **safety:** class-based self-narrative claim detector (B3) ([40277a7](https://github.com/niansahc/ember-2/commit/40277a735808c215954c7cfd98050055bcd4608a))
* **safety:** coaching filter expansion for B6 engagement-question + B7 circular dodge ([cad08cb](https://github.com/niansahc/ember-2/commit/cad08cb032fc3c7b3613c33ead2d9ca1071aa9ef))
* **safety:** expand coaching filter for engagement-question (B6) and circular dodge (B7) ([359d480](https://github.com/niansahc/ember-2/commit/359d480f68c62acfefa186f1df44d523a24dbd8b))
* **safety:** handle malformed markdown and bidirectional path-prefix in URL validator ([6fe4bac](https://github.com/niansahc/ember-2/commit/6fe4bac73173c396dc73b273db5a2df8c63f5ff2))
* **safety:** Jaccard pre-filter for template_collapse false negative (B5) ([19a6afd](https://github.com/niansahc/ember-2/commit/19a6afd056ba05e8a4ef80b7b8798576bdba2c1d))
* **safety:** third B6 engagement-closing pattern for list/table/agenda variant ([3cd82f2](https://github.com/niansahc/ember-2/commit/3cd82f226964417c3cdd2a7fc26eeb5e015dba7c))
* **sse:** emit status as top-level typed frame, ADR-040 v2 (B-SSE-001) ([22d9d41](https://github.com/niansahc/ember-2/commit/22d9d4181f51021d4be85d83115056b315ca5aeb))
* **sse:** emit status as top-level typed frame, ADR-040 v2 (B-SSE-001) ([d606b83](https://github.com/niansahc/ember-2/commit/d606b835cebd7d7bffd1e5fc22369906d5b72f18))
* **state:** make pending_confirmation resolution append-only via derived resolved-set (A2) ([835d107](https://github.com/niansahc/ember-2/commit/835d1071130424a1d8eac0f820baecfdd1e33e3f))
* **state:** make pending_confirmation resolution append-only via derived resolved-set (A2) ([04eec4c](https://github.com/niansahc/ember-2/commit/04eec4c191ce296c2642fe0fe38a92a222569883))
* **state:** suppress resolved open_loop originals at read time (B-STATE-001) ([dd4def7](https://github.com/niansahc/ember-2/commit/dd4def7dc68b3328876e8bd9cafec9e20f105a93))
* **state:** suppress resolved open_loop originals at read time (B-STATE-001) ([095351f](https://github.com/niansahc/ember-2/commit/095351f8c16e4c4863cd80fda4122b44d9b20cbe))
* **vision:** short-circuit on preprocess failure; move default to qwen3-vl:8b ([3cfaa02](https://github.com/niansahc/ember-2/commit/3cfaa02c01c8fdc9512b918e37258c9f9fd63b28))
* **vision:** short-circuit on preprocess failure; move default to qwen3-vl:8b ([e90317c](https://github.com/niansahc/ember-2/commit/e90317c17059a568d5cde5938de40d72d0a8e39a))


### Performance Improvements

* **state:** pass pending_confirmation records through to skip second vault scan ([37c0a9b](https://github.com/niansahc/ember-2/commit/37c0a9bb3b7199959d2c08eba74f7177423bec92))


### Miscellaneous Chores

* release 0.18.0 ([f2590ca](https://github.com/niansahc/ember-2/commit/f2590caf8bf1659f36b1007271d057de1826d358))

## [0.17.1](https://github.com/niansahc/ember-2/compare/v0.17.0...v0.17.1) (2026-04-30)


### Features

* **api:** invoke detect_t2_pattern after context build ([3d37853](https://github.com/niansahc/ember-2/commit/3d3785340df509e18a782f90b5bad7a5713bf7d7))
* **context:** t2_pattern_signal field + surface cached embedding ([168d4b0](https://github.com/niansahc/ember-2/commit/168d4b0c7c9aaf9a6c90157e36540e36a5560377))
* **llm:** resolve Item 7 ITEM-8 marker — read t2 category from packet ([609e859](https://github.com/niansahc/ember-2/commit/609e859a77d0c7ec2c2f2d6c1a90997b30b28497))
* **memory:** contains_named_third_party heuristic + tests ([d9e418f](https://github.com/niansahc/ember-2/commit/d9e418ff9085beb9e18252c8cdea3d99165160ef))
* **memory:** wire contains_named_third_party into write_memory ([09e81d5](https://github.com/niansahc/ember-2/commit/09e81d5cf4fbb02e3075d72d99ca2eb0f966d607))
* **prompt:** inject &lt;cross_session_pattern&gt; block when signal present ([b13d65f](https://github.com/niansahc/ember-2/commit/b13d65f004d04af89ff4154f3e8578c2df39ee68))
* **reflection:** Item 9 — Lodestone path 2 inferred-value synthesis (ADR-017) ([942dba8](https://github.com/niansahc/ember-2/commit/942dba8e05f1a996c48120ca704e381d250085b8))
* **reflection:** lodestone path 2 synthesis (ADR-017) ([787263a](https://github.com/niansahc/ember-2/commit/787263a2d04f4fa8b05e1ee90cd3407dd6a2638f))
* **reflection:** wire path-2 synthesis into monthly runner ([80e217f](https://github.com/niansahc/ember-2/commit/80e217fd63d2680cb3098eb5bbaa20807fb2374c))
* **safety:** add is_vault_grounded + t2_pattern_category to SafetyReviewContext ([f2a990b](https://github.com/niansahc/ember-2/commit/f2a990be293f7eaa31f5e8be3220576abdbe689f))
* **safety:** Item 7 — ADR-035 constitutional review context signal ([6fde4e3](https://github.com/niansahc/ember-2/commit/6fde4e37b34f8465daa1ce226d9ddeddb09e024d))
* **safety:** Item 8 — T2 cross-session pattern detection (ADR-021) ([6329624](https://github.com/niansahc/ember-2/commit/6329624a85053c93f0e048d418b071b5665e6a3d))
* **safety:** PatternSignal + detect_t2_pattern (ADR-021) ([ac90735](https://github.com/niansahc/ember-2/commit/ac90735fed3de7abfcd5d02f47dce946c043aec3))
* **safety:** thread is_vault_grounded + t2_pattern_category into review ([66cf6d1](https://github.com/niansahc/ember-2/commit/66cf6d161f01684c446549a15fc62483fb041b30))
* **safety:** two-step review prompt when t2_pattern_category is set ([9ea5774](https://github.com/niansahc/ember-2/commit/9ea577422d676c1229090233aa229e53e355b6f7))
* **uat:** add --ids flag for targeted re-run with result merging ([55edfea](https://github.com/niansahc/ember-2/commit/55edfead1eb96a79137396ecb4f8bc4ef3428908))
* **vision:** add file logging to vision pipeline (logs/vision/) ([8122a6a](https://github.com/niansahc/ember-2/commit/8122a6abb6819e87648aee6a1a0e9d890c9a42cf))


### Bug Fixes

* **api:** remove inline __EMBER_API_KEY__ injection in _get_index_html ([ca9cd12](https://github.com/niansahc/ember-2/commit/ca9cd12d5d73f20450b620f31a679558e6e4eeff))
* **api:** respect classifier intent over conversational keyword heuristic in web-search backstop ([9adffba](https://github.com/niansahc/ember-2/commit/9adffba90b3fe3219e7421221a4482e480294fb4))
* **context:** expand routing rules for pet and routines queries — vault-first ([42c7796](https://github.com/niansahc/ember-2/commit/42c7796360b6aa06396aa32471799023881c793f))
* **core:** B-WEB-001 sentinel-gated preference migration ([3bb7dce](https://github.com/niansahc/ember-2/commit/3bb7dce370389cc2709738e5dd319425f46df9a1))
* **ingestion:** date rendering reflects event date not ingestion date ([918a268](https://github.com/niansahc/ember-2/commit/918a2685cb8a04c78d60b67a40fcc06f93710887))
* **intent:** classify conversational acks as vault_answerable at Stage 1 ([af8b68b](https://github.com/niansahc/ember-2/commit/af8b68bf0d410ecfc0783faf9c4eae6f639fbc16))
* **llm,core,api:** S1-S9 correctness gaps from agent review ([c4469b5](https://github.com/niansahc/ember-2/commit/c4469b5afeb9a975ee3d3c3e00b89fdd37bfc11a))
* **llm:** add explicit type inventory to vault context — surfaces absent record types ([aa4f09d](https://github.com/niansahc/ember-2/commit/aa4f09d7162f00c0aeab730a09529ff9439c981c))
* **llm:** add intent_class param to generate_response (Fix 2 follow-up [#2](https://github.com/niansahc/ember-2/issues/2)) ([4ffa5b1](https://github.com/niansahc/ember-2/commit/4ffa5b1586e375bd40b5f2ff80358955b8f3994d))
* **llm:** B-MEM-003/004/005 hedge repetition fixes ([79d7868](https://github.com/niansahc/ember-2/commit/79d786846bda072bf12b47efe26d27e56fd21e90))
* **llm:** B-MEM-005 anti-URL rule (partial mitigation) ([ec0f98b](https://github.com/niansahc/ember-2/commit/ec0f98b9a4f92b9357f554aff989922073013015))
* **llm:** B-QUAL-001 dynamic num_ctx resolution per model ([77ee14d](https://github.com/niansahc/ember-2/commit/77ee14d2de6d5b742aac1a3569660ca6706191f3))
* **llm:** B-QUAL-002 therapeutic closing question patterns ([3229dfb](https://github.com/niansahc/ember-2/commit/3229dfb53470dccf46d0af78096a1310896286fb))
* **llm:** B-QUAL-004 empty retrieval grounding guard ([4d2611c](https://github.com/niansahc/ember-2/commit/4d2611c9cd10ee721f5ef24cb72f83dc9f1cf53e))
* **llm:** gate ZERO confidence block on intent_class — only fires on personal vault queries ([3060cfd](https://github.com/niansahc/ember-2/commit/3060cfdd5b3348eded49e6c00f6225c2d252dc7b))
* **llm:** include year in date rendering for records older than 365 days ([e5aa8db](https://github.com/niansahc/ember-2/commit/e5aa8db9291d51a1d5a40d57912a60ad099e34c0))
* **llm:** mask BEHAVIOR RULES and IDENTITY UNDER PRESSURE prompt section labels ([64c8e74](https://github.com/niansahc/ember-2/commit/64c8e746aa6b677f4d25b2019557fa52ecc99019))
* **llm:** mask vault_memory internal label from user-visible responses ([5ae469e](https://github.com/niansahc/ember-2/commit/5ae469e8d596fa25fa01d20bc9217d3822de2426))
* **llm:** prevent mid-sentence response truncation ([5304c57](https://github.com/niansahc/ember-2/commit/5304c575985a91748a1b45636d38a7fc6ebfed64))
* **llm:** remove dead True-or-X condition in openai_adapter ([6624b68](https://github.com/niansahc/ember-2/commit/6624b68b3bd79db009205c671fd49bafe8d73a9b))
* **llm:** wire intent_class to _render_authority_rules call site (Fix 2 follow-up) ([963b579](https://github.com/niansahc/ember-2/commit/963b57928c77e4149ade9ecc67b398f48528f569))
* **reflection:** Item 9 review-agent cleanup ([d350db8](https://github.com/niansahc/ember-2/commit/d350db8b23e2506351a17927b8220ca22608a25b))
* **retrieval:** named entity in query as primary ranking discriminator ([a89c7d0](https://github.com/niansahc/ember-2/commit/a89c7d0039503793e6f602cb2b29e9257c01abc2))
* **safety,llm,core:** M1 false-positive + S3-S6 correctness and privacy fixes ([3592633](https://github.com/niansahc/ember-2/commit/35926331f6cae4b87fac8e6619b64db8be7396e9))
* **safety:** B-CON-002 three-layer identity collapse defense ([3ed9adc](https://github.com/niansahc/ember-2/commit/3ed9adcad05d764f0ed489d217868100610fa4c1))
* **safety:** Item 8 simplify — timestamp bug, dead state, type hints ([3cab2b1](https://github.com/niansahc/ember-2/commit/3cab2b1c93ffc590037da3186c4cff4eb139aadc))
* **safety:** Item 8 simplify — timestamp bug, dead state, type hints ([0d6bc92](https://github.com/niansahc/ember-2/commit/0d6bc92a10be6a36fded36679c705bfa367582a5))
* **security:** add Content Security Policy headers to API responses ([5981c92](https://github.com/niansahc/ember-2/commit/5981c925448c9e7858824e61cfed2451548b5fa1))
* **security:** route social_engineering triggers to grounded streaming path (ADR-036) ([d6c257a](https://github.com/niansahc/ember-2/commit/d6c257a325c604ff86fa6677a6cb9977e5bf31fb))
* **state:** fix diagnostic log level info→warning so EMBER_STATE_DEBUG entries surface in uvicorn output ([2fe3a36](https://github.com/niansahc/ember-2/commit/2fe3a367d7eab0a9f39d30eda7e551fb993a1b40))
* **state:** restore cross-session state record persistence ([2d6264e](https://github.com/niansahc/ember-2/commit/2d6264ea71941156a207807e4f0fc1cf94200ef5))
* stop letting the keyword heuristic override the classifier. ([9adffba](https://github.com/niansahc/ember-2/commit/9adffba90b3fe3219e7421221a4482e480294fb4))
* **vision:** clear image_data after successful VL preprocessing ([a1c98c2](https://github.com/niansahc/ember-2/commit/a1c98c2f335ecb98ddbfe02e27a75c1fc6ebb6d9))
* **vision:** VisionService env-var resolution + clear image_data after preprocessing ([6e447ed](https://github.com/niansahc/ember-2/commit/6e447ed36994c263a07efe967f3184dc6388d86c))
* **vision:** VisionService honors EMBER_VISION_MODEL env var ([d76ed58](https://github.com/niansahc/ember-2/commit/d76ed58492313b504fe56a04a0fe3814d909b530))

## [0.17.0](https://github.com/niansahc/ember-2/compare/v0.16.0...v0.17.0) (2026-04-24)


### Features

* add explicit anti-sycophancy and register rules to instruction section ([fcfb4d2](https://github.com/niansahc/ember-2/commit/fcfb4d256ee1266abd219507428977e3c3994766))
* **api:** add POST /v1/service/shutdown for UI shut-down button ([4fd4a8d](https://github.com/niansahc/ember-2/commit/4fd4a8d74631527652915ddc7418e285341e643d))
* **api:** add POST /v1/service/shutdown for UI shut-down button ([9202fd1](https://github.com/niansahc/ember-2/commit/9202fd1097c0b50b4619cff7be7d09e53ff99530))
* ask-first intent classifier (three-tier hybrid) ([d55ba86](https://github.com/niansahc/ember-2/commit/d55ba8611229522ca2a19c122477cb6dfcf3955c))
* **context:** integrate intent classifier into classify_query ([fb9bf09](https://github.com/niansahc/ember-2/commit/fb9bf09cc7f55b0dd0c0793e1a909d454884257f))
* extend coaching_filter with additional sycophancy and therapeutic register patterns ([958b720](https://github.com/niansahc/ember-2/commit/958b72013e92fa5b973fdab7b271731e58a5f7ea))
* extend nature entries with anti-sycophancy and anti-softening language ([223b29f](https://github.com/niansahc/ember-2/commit/223b29f28030997c831f295ebea61c34949d1cd3))
* **ingest:** skip embedding for assistant-role chunks from ChatGPT import ([15f598e](https://github.com/niansahc/ember-2/commit/15f598e057a354432619d3f664130b37e93a59c7))
* **llm:** intent classifier stage 1 structural rules ([07b9ba8](https://github.com/niansahc/ember-2/commit/07b9ba81f322cfbd35f95280c9cc9833d934d97a))
* **llm:** intent classifier stage 2 embedding similarity ([d5c6340](https://github.com/niansahc/ember-2/commit/d5c63401525a79d1831cdfc10d145dcac1899384))
* **llm:** intent classifier stage 3 llm fallback with timeout ([1e4afa2](https://github.com/niansahc/ember-2/commit/1e4afa2484ad363a67166b9c9eb8e0b068908abb))
* qwen3:8b response quality (A-001 sycophancy, M-001 register) ([0305bf3](https://github.com/niansahc/ember-2/commit/0305bf34beea4ad8fc55cf9492c8fdadfe083b87))
* replace UAT suite with 22 behavioral acceptance tests ([1538232](https://github.com/niansahc/ember-2/commit/15382320e497e48072f3713f64c6682ca55fab59))
* replace UAT suite with 22 behavioral acceptance tests ([522b88a](https://github.com/niansahc/ember-2/commit/522b88a1cbd6200b49d2362df361e1113e91bb2f))
* **state:** gate StateExtractor to live conversation turns only ([7b99eb7](https://github.com/niansahc/ember-2/commit/7b99eb758ed112384cc97396f923215aef66fcb4))
* **uat:** auto runner skips entries with type: manual ([8a18064](https://github.com/niansahc/ember-2/commit/8a180648f369b27f4a107f60f7da236230ab09db))
* **uat:** automated runner with Claude-as-judge for 12 of 22 tests ([c322382](https://github.com/niansahc/ember-2/commit/c322382eb54bd5d4603541d8a50b121649b1bf7c))
* **uat:** automated runner with Claude-as-judge for 12 of 22 tests ([5d65d41](https://github.com/niansahc/ember-2/commit/5d65d411c86528ce6879077280bea6c784fa188e))
* **uat:** extend type: manual skip to dry_run classifier ([1a995c9](https://github.com/niansahc/ember-2/commit/1a995c97e5ae027447a866c976a3a780a3ec3f3e))
* **uat:** timestamped per-run report files; add --release flag ([50dee58](https://github.com/niansahc/ember-2/commit/50dee5899c69d7e18a0b00f03729fbf2d8cce9ca))
* update current state docs for v0.17.0 ([7ef11e7](https://github.com/niansahc/ember-2/commit/7ef11e784b7048830ea6e5f8a68feea96262f796))


### Bug Fixes

* **logging:** remove em dashes from logger calls (Windows cp1252) ([b7e27f9](https://github.com/niansahc/ember-2/commit/b7e27f9f845b890da21944470c19b218f9a84c60))
* **logging:** remove em dashes from logger calls (Windows cp1252) ([0ca80c3](https://github.com/niansahc/ember-2/commit/0ca80c3072a0564f9712328f29f18513aa7e2b20))
* **state:** ASCII-only skip log message for Windows cp1252 ([d7fb0fd](https://github.com/niansahc/ember-2/commit/d7fb0fd0b8966458b894df0f1d2f3df2fb63fbd6))
* **tasks:** match reminder phrasing with time qualifiers and polite wrappers ([5ff8e06](https://github.com/niansahc/ember-2/commit/5ff8e06c42c5d922386e85aa1ff2d5ff04728127))
* **tasks:** match reminder phrasing with time qualifiers and polite wrappers ([03a3a7d](https://github.com/niansahc/ember-2/commit/03a3a7da3c5167ccf6b71d2ec8f5a2b6d857ef89))
* **uat:** bump runner httpx timeout 120 -&gt; 180 seconds ([276644d](https://github.com/niansahc/ember-2/commit/276644d79c651ff096f1c550d34e8ac858055f79))
* **uat:** bump runner timeout to 180s + recover orphaned commits ([724e5e0](https://github.com/niansahc/ember-2/commit/724e5e01197f7b79810a3584d46963939a1bf485))
* **uat:** correct keyring path for Anthropic API key ([7c537b0](https://github.com/niansahc/ember-2/commit/7c537b026d777e2693da96392f8b7e3b65c1144d))

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
