# Spec Review 03 — specs de entrada (050, 060, 070, 080, 090, 100)

**Fecha:** 2026-11-08 · **Modo:** read-only (no se editó ninguna spec) · **Revisor:** Spec Reviewer (SDD paso 2)

Leído en el orden mandatado: `README.md`, `_template.md`, `DECISIONS.md` (D-001…D-007), `INDEX.md`,
`000-system-architecture.md`, `010-data-model.md`, `020-domain-core.md`, `030-sqlite-adapter.md` (§2.2,
§2.5 SQL-4/SQL-6, §2.6 read models), `040-application-services.md`, y las seis specs bajo revisión.

---

Verdict: FAIL
Specs revisados: 050-cli.md, 060-api-rest.md, 070-web-dashboard.md, 080-transfer-csv-json.md, 090-search-fts.md, 100-config-bootstrap.md
Gaps: 34 (bloqueantes: 18, menores: 16)

---

## Bloqueantes

### 1. [SPEC-050 §2.5.1] BLOQUEANTE — `init` siembra "5" estados y el set no coincide con 040 ni con 100

**Exige:** un único conjunto de `workflow_state` inicial, con nombres, tipos, colores y posiciones fijos.
**Dice:**
- `050-cli.md:419` — "los **5** `workflow_state` por defecto".
- `050-cli.md:423-426` — lista **6**: `Backlog`/`backlog`/`#6B6F76`/1.0, `Todo`/`unstarted`/`#E2E2E2`/2.0
  (`is_default=1`), `In Progress`/`started`/`#F2994A`/3.0, `In Review`/`started`/`#5E6AD2`/4.0,
  `Done`/`completed`/`#4CB782`/5.0, `Canceled`/`canceled`/`#6B6F76`/6.0. **No hay `Triage`; hay `In Review`.**
- `050-cli.md:946` (AC) — "el team `ENG` tiene **6** workflow states".
- `040-application-services.md:940-942` (provenance) — 6 = Backlog, Todo, In Progress, **In Review**, Done, Canceled.
- `100-config-bootstrap.md:217-224` — `DEFAULT_STATES` = **Triage**, Backlog, Todo, In Progress, Done, Canceled
  (`In Progress` `#F2C94C`, `Done` `#5E6AD2`, `Canceled` `#95A2B3`); **no hay `In Review`**.

Tres conjuntos distintos para el mismo seed, y un "5" que no cierra con ninguna lista.
**Cambiar:** un solo seed canónico en `SPEC-100 §2.5` (ya es dueño del bootstrap), `050:419` pasa a "6" y su
§2.5.1 referencia la tabla de 100 en vez de repetirla; `040` ajusta su provenance al set final. Si se
conserva `In Review` hay que declarar 7 estados y corregir `100`, `070` (que espera 6 columnas) y `040`.

### 2. [SPEC-050 §2.5.1 vs SPEC-100 §2.7] BLOQUEANTE — dos `hifa init`: flags, alcance y sobre JSON distintos

**Exige:** el mismo comando, con los mismos flags y el mismo documento `--json`.
**Dice:**
- `050-cli.md:413` — flags `--name`, `--team-key`, `--team-name`, `--db`, `--force`; `--json` =
  `{workspace: {id, name, url_key}, team: Team, states: State[], db_path}`.
- `100-config-bootstrap.md:304` — flags `--team-key`, `--team-name`, `--icon`, `--color`, `--timezone`,
  `--workspace-name`, `--no-labels`, `--no-member`, `--force`, `--db`; `--json` = `BootstrapResult`
  (`created`, `skipped`, `workspace_id`, `team_id`, `member_id`, `db_path`, `already_initialized`).
- `050-cli.md:418-419` — `init` crea workspace, team, estados y `.hifa.toml`, **sin labels ni member**;
  `100-config-bootstrap.md:341-343` siembra además 4 labels y 1 member + `team_membership`.

`--name` vs `--workspace-name` y dos sobres incompatibles: el mismo comando no puede cumplir ambas tablas.
**Cambiar:** `050` adopta literalmente la fila de `100 §2.7` (flags + `BootstrapResult`) y borra su propio
JSON; `050` menciona labels y member en el alcance de `init`.

### 3. [SPEC-050 §2.3 vs SPEC-040 §2.2/§2.3] BLOQUEANTE — la CLI declara un puerto de aplicación inventado y la reconciliación va al revés

**Exige:** los bordes construyen el comando de `040` y llaman la función de `040`.
**Dice:**
- `050-cli.md:162-250` — firmas planas por keyword: `create_issue(uow, *, team, title, …)`,
  `update_issue(uow, *, ref, changes: dict)`, `move_issue(uow, *, …, sort_order)`,
  `list_issues(uow, *, …, cursor) -> Page[Issue]`, más símbolos que **no existen** en `040`:
  `bootstrap_workspace`, `list_teams`, `get_team`, `assign_issue`, `add_labels`, `list_comments`,
  `add_attachment`, `delete_issue`, `list_projects`, `get_project`, `list_cycles`, `get_cycle`,
  `close_cycle`, `create_member`, `list_members`, `get_member`, `set_member_active`, `create_label`,
  `list_labels`, `archive_label`, `export_records`, `import_records`, `db_health`, `vacuum_db`,
  `environment_health`, `search_issues`.
- `040-application-services.md:48-60` — 7 módulos (`commands`, `issues`, `teams`, `labels`, `projects`,
  `cycles`, `boards`), comandos dataclass, sin member ni attachment.
- Dirección del arreglo invertida: `040:374-375` dice "esa discrepancia es drift y se corrige en `SPEC-050`
  con bump de `version`", mientras `050:165-166` dice "se corrige en **040**".

**Cambiar:** `050 §2.3` se reescribe como tabla de invocación (`CreateIssue(...)` + `Actor`) tomando
`040:376-394` como única fuente; todo símbolo que `050` necesite y `040` no tenga se agrega a `040 §2`
(con su transacción y su fila en `§3.1`), no a `050`. Fijar una sola dirección: manda `040`.

### 4. [SPEC-050 R19 + §7 AC vs D-001] BLOQUEANTE — `doctor` sigue documentado con el mapa viejo de exit codes

**Exige (D-001, `DECISIONS.md:12-20`):** `doctor` usa `0` ok, `1` warn, `2` fail, `3` no diagnosticable.
**Dice:**
- `050-cli.md:841` — "sale **0 con `warn`**, **6** con cualquier `fail`".
- `050-cli.md:1058-1062` — AC "doctor falla con exit **6** si el esquema está atrasado".
- Contradice su propia tabla: `050-cli.md:414` ya apunta a `SPEC-100 §6`, y `100:472-474` fija 0/1/2/3.

El AC y la regla son normativos y van a producir tests que exigen 6.
**Cambiar:** `R19` → "0 sin hallazgos, 1 con `warning`, 2 con `critical`"; el AC de `doctor` pasa a
`exit 1` (warning) y se agrega un escenario con `critical` ⇒ `exit 2` y otro con base ausente ⇒ `exit 3`.

### 5. [SPEC-050 §2.6 + SPEC-020 §6 + SPEC-040 §6 + SPEC-100 §6] BLOQUEANTE — el exit `1` existe en tres specs y no existe en el mapa de la CLI

**Exige:** una sola tabla de exit codes (A7).
**Dice:**
- `020-domain-core.md:769` — `HifaError`/`HIFA_ERROR` ⇒ **exit 1**, HTTP 500.
- `040-application-services.md:656` — `RANKING_DEGRADED` ⇒ **exit 1**; `040:661` — `UOW_NESTED` ⇒ **1**.
- `050-cli.md:527-535` — la tabla tiene 0,2,3,4,5,6,130: **no hay 1**; `050:897` manda todo
  `Exception` no mapeado a 6.
- `100-config-bootstrap.md:599-601` — "El `1` es **exclusivo** de `doctor`".

Un bug interno produce 1 en `020`/`040` y 6 en `050`, y en `100` el 1 está reservado para `warn`.
**Cambiar:** decisión explícita (recomendado: `020 §6` y `040 §6` pasan `HIFA_ERROR`/`RANKING_DEGRADED`/
`UOW_NESTED` a **exit 6 + `internal_error`**, dejando el 1 solo para `doctor`), con bump de `spec_revision`
en `020`, `040` y `050`, y nota en `Notes` apuntando a D-001.

### 6. [SPEC-050 §2.2.4 vs SPEC-100 §2.3] BLOQUEANTE — dos formatos incompatibles de `.hifa.toml`

**Exige:** un solo formato de archivo de configuración.
**Dice:**
- `050-cli.md:149-157` — `[workspace] name/db` + `[defaults] team/member`.
- `100-config-bootstrap.md:161-180` — `[workspace] name/db/url_key` + `[team] key/name/icon/color/timezone`
  + `[cli] default_team` + `[web] host/port`.

`050` resuelve el team en `defaults.team` (`050:128`) y `100` en `[cli].default_team` (`100:192`); el
`member` por defecto de `050:137` no tiene clave en el formato de `100`. Un archivo escrito por `init`
(que sigue `100`) no es leído por `050`.
**Cambiar:** `100` es dueño del formato; `050 §2.2.2/2.2.3/2.2.4` referencian `[cli].default_team` y se
agrega `[cli].default_member` a la tabla de `100 §2.3` (o se elimina la resolución de member por config y
solo queda `--member`/`HIFA_MEMBER`).

### 7. [SPEC-050 §2.5.1 vs SPEC-090 §2.5] BLOQUEANTE — `hifa search` declara 2 flags; `090` declara 11

**Exige:** misma superficie de flags (regla R17: "exhaustivo y cerrado").
**Dice:**
- `050-cli.md:416` — `hifa search QUERY` con `--limit` (1-200, default 50) y `--team`, nada más.
- `050-cli.md:839` — R17/REQ-F050-03: "Todo comando hoja declarado existe con **exactamente esos flags**;
  no hay comandos ni aliases no declarados".
- `090-search-fts.md:187` — `--team --state --assignee --label --priority --due --is --limit --offset --all
  --count --sort --json`.

**Cambiar:** `050` adopta la fila completa de `090` (D-002 ya le dio a `090` la autoridad del sobre; extender
la delegación a los flags y decirlo en `050 §11 Notes`), o `090` retira esos filtros a favor de la sintaxis
embebida `campo:valor` y lo declara en §2.5.

### 8. [SPEC-050 §2.5.5 vs SPEC-100 §2.7] BLOQUEANTE — `db reindex` / `db doctor` / `db path`: sobre y árbol inconsistentes

**Exige:** una fila por comando, con el mismo `--json` y los mismos exit codes.
**Dice:**
- `050-cli.md:517` — `hifa db reindex` ⇒ `{rebuilt: bool, rows: int}`, exit `0,6`.
- `100-config-bootstrap.md:307` — `hifa db reindex` ⇒ `{"reindexed": 10000}`, exit `0,3`; `100:478-481`
  fija que es la acción correctiva y que devuelve el `COUNT(*)` de `issue`.
- `050-cli.md:518` — `hifa db doctor` ⇒ `{ok, checks}` exit `0,3,6`; `100` no lo define (`100:305` solo
  `hifa doctor`) y `100:599` prohíbe inventar códigos fuera de 0/1/2/3 para `doctor`.
- `100-config-bootstrap.md:306` — `hifa db path` existe y no figura en el árbol cerrado de `050` (R17).

D-003 se cumple (el comando vive en `hifa db reindex` y no como flag de búsqueda), pero el contrato de
salida está duplicado y divergente.
**Cambiar:** una sola fila canónica (`100 §2.7`): `{"reindexed": N}`, exit `0,3`; `050:517` la copia igual.
Eliminar `hifa db doctor` de `050:518` (duplica `hifa doctor`) o declararlo alias explícito del mismo
handler. Agregar `hifa db path` a `050 §2.5.5`.

### 9. [SPEC-050 §2.5.3 + SPEC-060 §2.5.1 vs SPEC-030 R11] BLOQUEANTE — `--hard` contradice "nada se borra físicamente"

**Exige:** `030:680-682` R11 y `030:714` REQ-F050: "Ninguna operación del adaptador ejecuta `DELETE FROM
issue`; `trash`/`archive` son UPDATE". `010:36` D4 separa archivar ≠ cancelar ≠ borrar.
**Dice:**
- `050-cli.md:474` — "`--hard` borra la fila (cascada a `issue_label`, `issue_relation`, `comment`,
  `attachment`, `issue_history`)".
- `060-api-rest.md:529` — `DELETE /api/v1/issues/{ref}?hard=true` ⇒ `204`; `060:851` R8 lo ratifica.
- `040-application-services.md:53` no tiene caso de uso de borrado duro (`RestoreIssue` es lo más cercano).

**Cambiar:** quitar `--hard`/`hard=true` de v1 (coherente con D4, D7 y `030 R11`) y dejar `delete` = soft
delete + `restore`; si se quiere conservar, se abre ADR, se cambia `030 R11`, se agrega `hard_delete_issue`
a `040 §2` con su fila en `§3.1`, y se sube `version` de `030`.

### 10. [SPEC-060 §2.5.4 + §2.6 + §2.7 vs D-002 / SPEC-090] BLOQUEANTE — `/api/v1/search` inventa otro contrato

**Exige (D-002, `DECISIONS.md:29-34`):** `--limit` 1-200 default 50 y sobre
`{hits, total, has_more, backend, took_ms}`; ítems `{issue, snippet, score}`. `090:51-52` fija
`DEFAULT_LIMIT=50`, `MAX_LIMIT=200`; `090:107-112` fija el `SearchPage`.
**Dice:**
- `060-api-rest.md:611` — `limit` **1-100, default 20**.
- `060-api-rest.md:651-656` — `SearchPage` = `{items, next_cursor, has_more, total_estimate}` (sin `backend`
  ni `took_ms`, sin `total`).
- `060-api-rest.md:794-797` — ejemplo con ese sobre y `next_cursor`.
- `060:679` — paginación keyset `(score, id)` para search, contra `090:337-340` (`limit+1` + `offset`).

**Cambiar:** `060` usa `limit` 1-200 default 50 y el sobre de `090` tal cual; `/api/v1/search` se declara
como adaptador de `SearchService.search` (mismos filtros, `offset`), no como una lista keyset propia.

### 11. [SPEC-060 §2.7] BLOQUEANTE — `score` con el signo invertido respecto de `090`

**Exige:** `090:104` "`score`: mayor = mejor"; `090:332` "`bm25` devuelve valores ≤ 0 … por eso el score
expuesto es su **negación**" (⇒ ≥ 0).
**Dice:** `060-api-rest.md:795-797` — `"score": -1.82`, `"score": -2.41`.

Quien implemente el ejemplo ordena al revés o expone un número negativo donde el contrato promete ≥ 0.
**Cambiar:** ejemplo con `score: 1.82` / `2.41` (o `4.31`), y una nota en `060 §2.4.4` de que el campo es
`-bm25` y por eso crece con la relevancia.

### 12. [SPEC-060 §2.5 + §2.4.4 vs SPEC-040 §2] BLOQUEANTE — endpoints y campos sin caso de uso detrás

**Exige:** cada handler valida forma, llama un caso de uso y serializa (`060:31-32`); `040` es la única
fuente de escrituras (`040:392-394`).
**Dice:** hay rutas y campos que no tienen función en `040`:
- `060:573-574` — `POST /teams/{ref}/states`, `PATCH /teams/{ref}/states/{state_id}`.
- `060:598-601` — `GET/POST/PATCH /members`.
- `060:539` — `POST /issues/{ref}/attachments` (y `060:204` de `050` es el único que menciona
  `add_attachment`).
- `060:532` — `PUT /issues/{ref}/labels` (reemplazo total, atómico); `040` solo tiene `add_label`/
  `remove_label`, y `set_labels` existe únicamente como método de repo (`030:281`).
- Campos de schema sin parámetro en el comando: `060:311` `TeamCreate.parent`; `060:321,326,328`
  `TeamPatch.visibility|default_issue_estimate|auto_close_period`; `060:413` `ProjectPatch.sort_order`.
  `040:202-260` no los declara (`UpdateTeam` ni siquiera tiene `visibility`).

**Cambiar:** agregar a `040 §2.1/§2.2` los comandos y casos de uso (`CreateMember`, `UpdateMember`,
`SetMemberActive`, `CreateState`, `UpdateState`, `AddAttachment`, `SetIssueLabels`) con su transacción,
su fila en `040 §3.1` y su código de error; o recortar `060 §2.5` a lo que `040` tiene. Los campos sin
parámetro se eliminan del schema.

### 13. [SPEC-060 §2.6 vs SPEC-030 SQL-4 / SPEC-040 §2.3] BLOQUEANTE — la paginación keyset no tiene soporte en la capa de datos

**Exige:** cursor opaco `base64url({"v":1,"o":<campo>,"d":<dir>,"k":[…],"id":"<ULID>"})`, orden
`{campo}.{asc|desc}`, orden total `(clave, id)`, `NULL` al final, `limit+1`, prohibido `OFFSET`
(`060:660-679`; igual en `050:829` R7 y `070:669` R22).
**Dice:**
- `030-sqlite-adapter.md:515-530` — `ListFilters` solo tiene `limit` y `offset`; no hay campo de orden ni
  de cursor.
- `030:410-412` — el `ORDER BY` de `list_view` es fijo (prioridad, `sort_order`, `id`): no se puede pedir
  `created_at.desc` ni `due_date.asc`.
- `030:425` — "`LIMIT` por defecto 50, **máximo 500**" contra `050:447`/`060:660` (máx 200).
- `040:387` — "`list_issues(db, ListFilters(...)) -> list[IssueListItem]`; paginación por `limit`/`offset`
  (SPEC-030 SQL-4), **sin cursor**".
- Además `050:255-274` declara su propio `IssueFilters` (plurales: `states`, `state_types`, `priorities`,
  `ids`, `open_only`, `only_archived`, `only_trashed`) que no es el `ListFilters` escalar de `030`.

Tres bordes exigen keyset y la capa que lo ejecuta declara explícitamente offset.
**Cambiar:** `030` agrega a `ListFilters` los campos `order_field`, `order_dir` y los valores de cursor
(`cursor_key`, `cursor_id`, más el predicado de tupla con la regla de `NULL`), expone `list_page(...) ->
(list[IssueListItem], has_more)` con orden total validado contra una allowlist, y sube `spec_revision`;
`040 §2.3` actualiza la fila; `030:425` pasa a 200 (o los bordes a 500) — un solo número. `050` usa
`ListFilters` de `030` en lugar de `IssueFilters`.

### 14. [SPEC-070 §5.1 + R21 vs SPEC-040 §2.2 / SPEC-090 §2.3] BLOQUEANTE — la web invoca funciones que no existen

**Exige:** los read models declarados son `board_view`, `list_view`, `issue_detail`, `agenda` (`030:57`,
`030:603-616`) y los casos de uso son los de `040 §2.2`.
**Dice:**
- `070-web-dashboard.md:695` — "`app.list_board(team, filters)`". No existe: `040:351-354` declara
  `get_board(db, *, team, group_by="state", limit_per_group=50)` y `030` no tiene `list_board`.
- `070-web-dashboard.md:668` — "ambas usan `app.search_issues` (SPEC-090)". `090:129-133` declara
  `SearchService.search(SearchQuery)`; `search_issues` solo aparece en el puerto inventado de `050:241`.
- `070 §2.3.3` usa `next_cursor`/`next_url`, que dependen del read model keyset inexistente (gap 13).

**Cambiar:** `070` nombra `reads.board_view`/`list_view`/`issue_detail` y `app.get_board`/`app.list_issues`
con las firmas de `030`/`040`, o esos specs agregan la función con la firma que la web necesita (y entonces
`040 §2.2` la lista). Un solo nombre por operación.

### 15. [SPEC-070 §2.6.4 / R9-R10 vs SPEC-040 R8 / §2.3] BLOQUEANTE — el drag & drop manda `sort_order` y `rebalance`, que `040` no acepta

**Exige:** `040:130-134` `MoveIssue(issue, target_state, position ∈ {first,last,before,after},
relative_to)`; `040:476-478` R8 calcula la posición con `ranking.between` y valida el vecino;
`040:382` "el `sort_order` explícito **no existe**: la posición se expresa por vecino".
**Dice:**
- `070:583-585` — la request lleva "`{"state_id": "...", "sort_order": 1536.0}` y nada más".
- `070:589-591` y `070:657` R10 — `"rebalance": true` cuando `|next − prev| < 1e-4`.
- `070:724` y `070:839` (AC) — mismo payload.
- `070:571-581` — el cliente calcula el fraccional; `020:452-479` (`ranking.between`) es el dueño de esa
  aritmética y `040 R9` del rebalanceo.

**Cambiar:** el fragmento recibe `state` + `before`/`after` (o `position`) y el cliente manda el
`identifier` del vecino; el servidor calcula con `ranking.between` y rebalancea con `040 R9`. Si se
quiere conservar `sort_order` explícito, `040` agrega el campo a `MoveIssue`, documenta la precedencia
(`position` vs `sort_order` vs `relative_to`) y elimina la frase "no existe" de `040:382`.

### 16. [SPEC-070 R15 / §2.3.4 vs SPEC-000 §8 / REQ-N007] BLOQUEANTE — el render de markdown no tiene implementación ni dependencia declarada

**Exige:** `070:244` y `070:662` R15 — `description_html` y `body_html` "ya escapados y con allowlist de
etiquetas (`p, br, strong, em, code, pre, ul, ol, li, a[href], blockquote, h1-h3`); `a[href]` solo
`http(s):`/`mailto:`", aplicable con `|safe`. `000:127-128` fija deps base `typer`+`pydantic` y extra
`web = [fastapi, uvicorn]`; AGENTS §9 exige justificar cada dependencia.
**Dice:** `070` no nombra módulo, función ni librería; `§2.4` (assets) no vendoriza nada de markdown y
`§8` (regeneration) tampoco lo menciona. `060:81` C9 lava las manos ("el HTML sanitizado es de SPEC-070").

Sin esto el componente no se puede regenerar desde su spec: no se sabe con qué se renderiza ni con qué se
sanitiza.
**Cambiar:** declarar en `070 §2` el contrato `render_markdown(text: str) -> str` (módulo `web/markdown.py`
o `app/markdown.py`), la allowlist exacta y la estrategia de escape, más la decisión de dependencia
(`markdown` + `bleach`, o un subconjunto propio) reflejada en `000 §8` y en `pyproject`. Agregar el
escenario de `[x](javascript:alert(1))` al módulo que corresponda.

### 17. [SPEC-080 R15 / R16 / R17 / §5.1] BLOQUEANTE — transaccionalidad por lote, `--strict` y `--dry-run` son mutuamente excluyentes

**Exige:** tres cosas a la vez.
**Dice:**
- `080:422-428` R17 — "Cada **lote** es una transacción"; SAVEPOINT por fila.
- `080:413-417` R15 — `--strict` "hace `ROLLBACK` de **todo lo escrito**… el reporte devuelve
  `created=0, updated=0, committed_batches=0`".
- `080:418-421` R16 — `--dry-run` "dentro de una transacción que **siempre** se revierte"; el reporte es
  "idéntico al de la corrida real salvo `dry_run=True` y `committed_batches=0`".
- `080:518-527` §5.1 — un **único** `with uow.write() as tx:` envuelve "para cada lote … COMMIT (o
  ROLLBACK si dry_run…)".

Si hay `COMMIT` por lote, los lotes anteriores ya están persistidos y ni `--strict` ni `--dry-run` pueden
revertirlos; y un solo `write()` de `030` es **una** transacción (`030:659-660` R3), así que el `COMMIT`
intermedio viola `030 §2.2`.
**Cambiar:** elegir el modelo y escribirlo una vez:
(a) una transacción por corrida (`batch_size` solo controla memoria y `executemany`) — entonces `--strict`
y `--dry-run` son trivialmente correctos y `committed_batches` se redefine; o
(b) commits por lote — entonces `--strict`/`--dry-run` fuerzan `batch_size = total` y el reporte de dry-run
se define aparte. En ambos casos el pseudocódigo de `§5.1` debe usar un `write()` por lote como máximo.

### 18. [SPEC-080 §2.3 vs §7] BLOQUEANTE — dos formas distintas del mismo comando

**Exige:** `080:249-250` — `hifa import FILE` con `--format {csv,json}` (default por extensión) y
`hifa export` con `--format`, `--out`.
**Dice:** todos los escenarios de §7 usan un subcomando posicional: `080:632`, `640`, `647`, `655`, `663`,
`671`, `678`, `685`, `694`, `702`, `710`, `717`, `724-725` — `hifa import csv issues.csv` /
`hifa export csv salida.csv`. `050:513-514` también declara la forma con `--format`.

Veinte escenarios de aceptación apuntan a un comando que la spec de al lado no declara.
**Cambiar:** fijar `hifa import FILE --format …` / `hifa export --format … --out …` (la de `050`) y
reescribir los escenarios; si se prefiere el subcomando, cambiar `050 §2.5.5` y `080 §2.3` juntos y
declarar los alias.

---

## Menores

### 19. [SPEC-080 §2.1 `JOURNAL_ORDER`] MENOR — el dump JSON omite dos tablas del esquema

`080:68-72` enumera 15 tablas, pero `010` define también `schema_migration` (`010:267`, migración 002) y
`team_previous_key` (`010:333`, migración 003, R3). Un "workspace completo" exportado/importado pierde el
historial de keys de team y el ledger de migraciones, sin que la spec lo diga.
**Cambiar:** incluir `team_previous_key` (después de `team`) y `schema_migration` (al final) en
`JOURNAL_ORDER`, o declarar explícitamente en §2.4 que no se exportan y por qué.

### 20. [SPEC-080 §2.1.1] MENOR — `tx.members` no existe en `Repos`

`080:203` y `080:206` fijan `get_by_email`/`find_by_display_name`/`insert` en `tx.members` "porque
SPEC-030 §2.4 no lista un `MemberRepo`", pero `030:128-137` define `Repos` con siete campos y **no** tiene
`members`, así que `tx.members` no resuelve.
**Cambiar:** agregar `members: MemberRepo` a `030:128` con la clase y sus firmas (bump de `spec_revision` en
`030`), o mover el acceso a `member` a un módulo de `adapters/sqlite/` propio de transfer y declararlo en
`080`.

### 21. [SPEC-080 R5 / R6] MENOR — duplica reglas del dominio y saltea `validate_title`

`080:354-361` reimplementa `validate_due_date` y `validate_estimate` (`020:507-520`), y `080:215` solo hace
`trim` del título sin el `len <= 255` de `020:490-496` (`MAX_TITLE`). `040:36-37` dice que duplicar una
regla en `app/` es un bug. Efecto: un CSV puede crear títulos de 10 000 caracteres que la CLI rechaza.
**Cambiar:** llamar `rules.validate_title`, `rules.validate_due_date`, `rules.validate_estimate` y borrar
las copias; agregar el caso de borde "título de 300 caracteres ⇒ `invalid_title`".

### 22. [SPEC-090 §2.2 vs D-002] MENOR — `SearchHit` no es el ítem `{issue, snippet, score}`

`090:92-104` define `SearchHit(issue_id, identifier, title, state_name, state_type, priority, due_date,
assignee_display_name, updated_at, snippet, score)`. D-002 (`DECISIONS.md:33`), `050:379-381` y
`060:497-501` usan `{issue, snippet, score}` con un objeto `issue` anidado. `090` no dice cómo se
serializa su dataclass al borde.
**Cambiar:** en `090 §2.2`, renombrar el dataclass plano a `SearchRow` y declarar
`to_item(row) -> {"issue": IssueOut, "snippet": str, "score": float}` (o el equivalente que arme el
borde), de modo que `050`/`060`/`070` tengan una sola forma.

### 23. [SPEC-090 §4 / §7] MENOR — sin tabla de `REQ-F` y sin tags de trazabilidad

Los `REQ-F090-01..17` se citan sueltos dentro de R1-R19 (`090:261-390`) pero no hay tabla que los defina
como en `020:682-700`; ningún escenario de §7 lleva `@REQ-F…`, y varios no tienen escenario
(REQ-F090-05 `query_too_long`, REQ-F090-06 filtros embebidos inválidos, REQ-F090-13 `due`).
**Cambiar:** agregar `§4.2 Requisitos funcionales` con la tabla ID/requisito/verificación y taggear los
escenarios (mismo patrón que `020 §7`).

### 24. [SPEC-070 §2.3 vs R3] MENOR — el filtro `default` está permitido y prohibido

`070:150` — Jinja puede usar "los filtros `default` y `join`"; `070:650` R3 — "Prohibido `default()`
defensivo en el template para una clave que el read model debe dar".
**Cambiar:** quitar `default` de la lista blanca (dejar `join`), o permitirlo solo en claves declaradas
opcionales y decirlo en `R3`.

### 25. [SPEC-070 §2.1.1 vs §9] MENOR — cookie `hifa_team` contra "sin estado en cookie"

`070:50` — `GET /` redirige al team de la "cookie `hifa_team`, si no el primero por `created_at`";
`070:951` (provenance) — "`/<team>/…` en la URL y **no** estado en cookie". Además "el primero por
`created_at`" es exactamente la selección arbitraria que `050:826` R4 prohíbe ("nunca se elige el primer
team por orden alfabético ni por `created_at`").
**Cambiar:** `/` redirige al `[cli].default_team` de `100`, y si no hay, a un selector de team (o a
`/search`); sin cookie y sin "primero por `created_at`". Corregir una de las dos frases.

### 26. [SPEC-070 R17 / §2.3.5 vs SPEC-090 R15] MENOR — doble escapado o XSS en el snippet de búsqueda

`090:362-367` devuelve el snippet **con** `<mark>`/`</mark>` ya insertados; `070:664` R17 dice "el snippet
se escapa primero y se envuelven los términos con `<mark>` **después**", y `070:279` muestra
`"…&lt;b&gt;<mark>login</mark> con <mark>SSO</mark>…"`. Escapando el string completo, las marcas de `090`
salen como texto literal; sin escapar, el texto del issue entra crudo en `snippet_html` con `|safe`.
**Cambiar:** fijar el algoritmo en `070` (split por `MARK_START`/`MARK_END` → `escape()` de cada segmento →
re-emisión de las marcas) y agregar el escenario "título con `<script>` no produce `<script>` en el HTML".

### 27. [SPEC-060 §2.3] MENOR — nombres y llamada al adaptador no coinciden con `030`

`060:91` — `open_connection(request.app.state.db_path, read_only=...)`; `030:75` es
`connect(db_path, *, readonly=...)`. `060:93` — `UnitOfWork(conn)`, pero `030:140-144` exige `clock` e
`idgen` keyword-only (`040:390` repite el atajo).
**Cambiar:** `connect(..., readonly=...)` y `UnitOfWork(conn, clock=…, idgen=…)` en `060 §2.3` y en
`040:390`.

### 28. [SPEC-060 §5.3 vs SPEC-040 §6] MENOR — `locked` mapeado a 503 contra `Conflict`/409

`060:922` — "`sqlite3.OperationalError (locked)` -> 503 `database_unavailable`"; `030:663` y `040:662`
traducen busy a `Conflict(code='DB_BUSY')` → 409, y `060:856` R13 prohíbe mapear por mensaje.
**Cambiar:** borrar esa línea de `§5.3`; `DB_BUSY` sale 409 por el `dict` de tipos. Si se quiere 503 para
"base no disponible", tiene que nacer de un error de dominio propio, no de `OperationalError`.

### 29. [SPEC-060 R2] MENOR — "superficie cerrada" choca con el dashboard, y la composición no está declarada

`060:845` R2 — "Las rutas registradas son exactamente las de §2.5… falla por cualquier diferencia"; pero
`hifa serve` monta también las páginas y fragmentos de `070 §2.1` en la misma app. Y ningún spec dice cómo
se compone `create_app(*, db_path, read_only)` (`060:48`) con el router web (`070:918`): orden de montaje,
`StaticFiles` en `/static`, middlewares de `070 R14`.
**Cambiar:** acotar R2/REQ-F060-02 a `/api/v1/*` + `/health`, y declarar la composición (por ejemplo
`create_app(..., mount_web: bool = True)` que incluya `web.install(app)`) en `060 §2.1` o en `070 §2.1`.

### 30. [SPEC-050 §2.5.1 / §2.5.5] MENOR — exit codes de más en `search` y `export`

`050:416` — `hifa search` con exit `0,2,3`; `090:187` declara `0,2` y `090:527-528` dice explícitamente que
la búsqueda "**no** produce `NotFound`, `Conflict` ni `InvalidTransition`". `050:513` — `hifa export` con
`0,2,3,6`; `080:250` declara `0,2,3` y `080:586` "nunca devuelve exit 1, 4 ni 5".
**Cambiar:** `search` ⇒ `0,2`; `export` ⇒ `0,2,3` (o agregar el 6 en `080 §6.1` si se quiere reservar para
bug interno, y justificarlo).

### 31. [frontmatter de las seis specs] MENOR — `status: draft` contra `INDEX.md: review`

`050-cli.md:6`, `060-api-rest.md:6`, `070-web-dashboard.md:6`, `080-transfer-csv-json.md:6`,
`090-search-fts.md:6`, `100-config-bootstrap.md:6` dicen `status: draft`; `INDEX.md:16-21` dice `review`
para los seis.
**Cambiar:** `status: review` en los seis (o cambiar el índice). Al aprobar, ambos pasan a `approved` en el
mismo commit.

### 32. [frontmatter `implements`] MENOR — globs solapados hacen ambiguo el drift check

`090:11` declara `src/hifa/adapters/sqlite/reads.py` y `030:10` declara `src/hifa/adapters/sqlite/**`;
`100:11-12` declara `app/teams.py` y `cli/commands/db.py`, `040:10` declara `app/**` y `050:10` declara
`cli/**`; `080:10` declara `app/transfer.py`, también dentro de `040:10`. Con la regla de `README:50-51`
(un cambio bajo un glob exige bump de esa spec), tocar `reads.py` obliga a bumpear dos specs.
**Cambiar:** excluir en el glob del dueño (`app/**` menos `transfer.py`, `search.py`, `teams.py`;
`adapters/sqlite/**` menos `reads.py`) o declarar en `README` que ante solapamiento gana la spec más
específica.

### 33. [SPEC-100 §2.2 vs SPEC-050 §2.2.1] MENOR — `050` pierde dos reglas de la resolución de la base

`050:110-115` lista 4 pasos y no dice qué pasa si el `.hifa.toml` no declara `db`; `100:142` fija
`<dir del .hifa.toml>/.hifa/hifa.db` y `100:143-144` separa `xdg` de `home` en dos fuentes con `source`
distinto.
**Cambiar:** `050 §2.2.1` referencia la tabla de `100 §2.2` (5 fuentes, incluido el default project-local)
en vez de repetir una versión incompleta.

### 34. [SPEC-060 §6.3 vs SPEC-020 §2.6 / §6] MENOR — el sobre de error tiene una clave más y `details` de otro tipo

`020:581-582` y `020:773-777` fijan `{"error": {"code", "message", "details"}}` con `details` como objeto
(`rule`, `field`, `value`); REQ-F023 (`020:697`) testea esa estructura exacta. `060:503-512` agrega
`field` como clave de primer nivel y tipa `details` como `list[dict]`; `050:396-399` adopta el sobre de
`060`. A7 exige un solo mapeo, pero el payload quedó con dos formas.
**Cambiar:** una de dos: (a) `020` agrega `field` a `to_dict()` y acepta `details: dict | list` (bump de
`spec_revision` + ajuste de REQ-F023 y de `020 §6`), o (b) `060`/`050` envuelven `to_dict()` y agregan
`field` **en el borde**, dejando `details` como objeto y declarándolo en `060 §6.3`.

---

## Verificado sin hallazgos

- **Frontmatter y 10 secciones:** las seis specs tienen las 12 claves obligatorias (`id`, `title`,
  `component`, `status`, `version`, `spec_revision`, `depends_on`, `implements`, `acceptance`, `owner`,
  `created`, `updated`) y las 10 secciones del template (§1 Responsabilidad … §10 Out of scope); 050, 090 y
  100 agregan §11 Notes apuntando a D-001/D-002/D-003. `acceptance:` apunta a
  `specs/acceptance/0NN-*.feature` en todas (la ausencia de esos archivos está registrada como P-01).
- **D-003:** `hifa db reindex` existe en `050 §2.5.5` y en `100 §2.7`, el hint de `doctor` apunta ahí
  (`050:758`, `100:597`) y `090:708` declara que el rebuild es explícito. Sin contradicción de fondo (solo
  el sobre JSON, gap 8).
- **D-001 en SPEC-100:** `R19` (`100:472-474`), la tabla de `§2.7`, `§6` y el AC (`100:715-750`) usan
  0/1/2/3 de forma consistente. La contradicción está en `050` (gap 4), no en `100`.
- **D-004:** IDs únicos y en formato canónico `REQ-F|N|S<NNN>-<NN>` en las seis; las referencias a
  `REQ-N001…`/`REQ-S00x` son globales de `000` y no son duplicados. Único defecto: `090` no define su tabla
  de REQ-F (gap 23).
- **060 y el DDL:** `IssueOut` (33 campos), `TeamOut`, `LabelOut`, `ProjectOut`, `CycleOut`, `MemberOut`,
  `CommentOut`, `RelationOut`, `StateOut` y `HistoryEntry` no inventan columnas; `priority_label`,
  `is_open`, `is_visible` salen de `v_issue` (`010:299-306`); `AttachmentOut.metadata` se documenta como
  JSON sobre `TEXT` (`060:514-516`). Los campos inventados están del lado de los *requests* (gap 12).
- **060 paginación:** formato de cursor, formato de `order`, rango de `limit`, `NULL` al final, `limit+1`,
  rechazo de cursor con `o`/`d` distinto y prohibición de `OFFSET` están definidos sin ambigüedad
  (`060:658-679`) — el problema es que `030`/`040` no lo soportan (gap 13).
- **070 Jinja / assets / progressive enhancement:** prohibición de lógica en Jinja con test estático
  (`R2`), `implements` de assets vendorizados con versión + licencia + `sha256` y presupuesto de 75 KB
  (`070:301-333`), cero CDN, `_/` separado de las páginas (`R1`), `hx-get` con `href` equivalente y
  `<noscript>` (`R4`, `070:614-621`).
- **080 REQ-S004 / REQ-S002:** validación de rutas con `resolve()` + `is_relative_to` + `lstat` +
  `tempfile`/`os.replace` (`080:452-458`, casos B17/B18) y SQL parametrizado con allowlist de tabla/columna
  (`080:459-463`) están completos y son testeables.
- **080 orden de FK del dump JSON:** `JOURNAL_ORDER` respeta padres→hijas para las FKs del DDL, ordena
  topológicamente las auto-referencias (`team.parent_id`, `label.parent_id`, `issue.parent_id`,
  `comment.parent_id`) y borra en orden inverso en `mode='replace'` (`080:441-447`). Solo faltan las dos
  tablas del gap 19.
- **090 sanitización y fallback:** normalización NFKC + control chars, términos siempre entrecomillados con
  comillas duplicadas, negación vía `NOT (...)`, `identifier` resuelto por SQL (no por `MATCH`), fallback
  `LIKE` con `escape_like` para `\ % _`, y límites 512 chars / 16 términos / 128 por término
  (`090:261-297`, `090:351-360`).
- **100 `0600`/idempotencia/sin tabla `settings`:** `R16` fuerza `0o700` en el directorio, `0o600` en la
  base y en `.hifa.toml` (`100:445-449`), `R11` hace get-or-create por clave natural en una sola
  transacción (`100:418-423`), y §1 prohíbe explícitamente una tabla `settings` aclarando que las
  "settings" son `.hifa.toml` + columnas de `team` (`100:35-38`). Ninguna de las seis specs inventa tablas.
- **Gherkin:** todos los `Scenario` tienen al menos un `When`; no hay bullets dentro de DocStrings (no se
  usan DocStrings).
- **Framing moralizante:** no aparece. `080:448-451` (CSV injection) y `060:857-859` (internals en la
  respuesta) son reglas técnicas, sin advertencias normativas ni secciones de "uso ético"/"fair use".

---

## Resumen para el Fix Agent

Orden recomendado de arreglo (dependencias primero): **13** (keyset en `030`/`040`) → **3** (puerto de
aplicación `050`↔`040`) → **1/2/6** (seed, `init` y `.hifa.toml` con `100` como dueño) → **12/14** (casos de
uso y read models faltantes) → **4/5/8/30** (tabla única de exit codes y sobres de `db`) → **7/10/11/22**
(contrato de búsqueda, con `090` como autoridad por D-002) → **9** (borrado duro) → **15/16/24/25/26**
(web) → **17/18/19/20/21** (transfer) → **23/27/28/29/31/32/33/34** (menores). Al terminar: `spec_revision`
en las seis (+ `version` en `030` y `040`, que cambian de contrato) y una entrada nueva en `DECISIONS.md`
para el exit `1` y para el modelo transaccional del import, porque son conflictos cross-spec y no se
resuelven "en el código".
