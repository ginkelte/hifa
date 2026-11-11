# Spec Review 01 — Ronda 2 (re-review)

```
Verdict: FAIL
Specs re-revisados: 000-system-architecture.md, 010-data-model.md,
                    001-mvp-v1.md, 001-mvp-v1.tasks.md,
                    acceptance/001-mvp-v1/*.feature (17 archivos)
Criterio: DECISIONS.md D-001..D-020 · spec-review-01.md (42 gaps) · _template.md
Gaps del review 01: 23 cerrados · 19 abiertos (13 bloqueantes, 6 menores)
Inconsistencias nuevas o residuales: 9 (2 bloqueantes)
```

Revisor: read-only sobre las specs. Único archivo escrito: este reporte. No se tocó git.
Todo lo afirmado abajo se verificó **contra el archivo en disco y ejecutando**, no contra los
mensajes de commit ni los reportes de los fix agents.

Herramientas: `sqlite3 3.53.1` (módulo `sqlite3` de CPython 3.11.15), `gherkin-official 42.0.1`
en venv `/tmp/rr1` (creado con `uv`). Scripts reproducibles en `/tmp/rr1/{ddl_check,gherkin_check,
coverage_check,quality_check,extra_check,smoke_check}.py`.

---

## 1. Evidencia de las cuatro verificaciones pedidas

### 1.1 D-008 — un solo DDL, y vive sólo en 010

| Chequeo | Resultado |
|---|---|
| `CREATE TABLE` en `001-mvp-v1.md` | **0 ocurrencias** (`grep -c "CREATE TABLE" 001-mvp-v1.md` → 0). 001:74-104 reemplaza el DDL por el mapa entidad→tabla + remisión a SPEC-010 §3. |
| `CREATE TABLE` en los 17 `.feature` | 0 ocurrencias |
| 010 con las 10 secciones del template | **10** (`§1..§10`), verificado con regex `^## (\d+)\. ` |
| `workspace_id` en `issue` y `project` | 010:236 (`issue.workspace_id TEXT NOT NULL REFERENCES workspace(id)`) y 010:192 (`project.workspace_id`) |
| `UNIQUE(team_id, id)` en `workflow_state` | 010:148 |
| `team_previous_key` con su `CREATE TABLE` | 010:418-429 (§3.6, migración 003, PK `(team_id,key)` + índice por `key`) |
| `schema_migration` como migración **002** | 010:404-413 (§3.5) y tabla de archivos 010:91-95 |
| 17 tablas | **17** aplicadas en sqlite (ver 1.2) |

### 1.2 D-009 / D-010 — DDL aplicado en sqlite3 (no leído)

Se extrajeron los **7 bloques ```sql** de `010-data-model.md` (líneas 102, 277, 330, 349, 377, 406,
420) y se aplicaron en orden sobre una base en memoria con `PRAGMA foreign_keys=ON`.
`DDL aplicado sin errores: OK`.

Estructura resultante:

```
tablas con nombre: 17 -> attachment, comment, cycle, issue, issue_history, issue_label,
  issue_relation, label, member, project, project_milestone, schema_migration, team,
  team_membership, team_previous_key, workflow_state, workspace
(+ issue_fts virtual, 4 shadow tables de FTS5, sqlite_sequence)
views (3): v_issue, v_priority, v_team_progress
triggers (3): issue_fts_ai, issue_fts_ad, issue_fts_au
indices (14): incl. uq_label_team, uq_label_ws
```

Pruebas ejecutadas (21/21 PASS):

| Caso | Resultado real |
|---|---|
| `due_date='2026-13-45'` | `CHECK constraint failed: due_date IS NULL OR (... AND date(due_date) IS due_date)` ✔ falla |
| `due_date='2026-02-30'` | mismo CHECK ✔ falla |
| `due_date='2026-3-1'` | ✔ falla (forma) |
| `due_date='2026-03-01'` / `NULL` | ✔ pasa |
| `priority=9` / `priority=-1` | `CHECK constraint failed: priority BETWEEN 0 AND 4` ✔ falla |
| `priority=4` | ✔ pasa |
| label de workspace duplicada (`team_id NULL`, `'Bug'`/`'Bug'`) | `UNIQUE constraint failed: label.workspace_id, label.name` ✔ falla |
| label `'Bug'` homónima en team `ENG` y en team `OPS` | ✔ pasan las dos |
| label `'Bug'` duplicada dentro del mismo team | `UNIQUE constraint failed: label.workspace_id, label.team_id, label.name` ✔ falla |
| `state_id` de otro team | `FOREIGN KEY constraint failed` ✔ falla (FK compuesta R4) |
| `issue.workspace_id` distinto al del team | `FOREIGN KEY constraint failed` ✔ falla (R8/§30) |
| mismo `number` en el mismo team | `UNIQUE constraint failed: issue.team_id, issue.number` ✔ falla |
| FTS: 3 INSERT → `issue_fts` 3 filas; DELETE → 2 vs 2 | ✔ triggers `_ai`/`_ad` vivos |
| `MATCH 't'` (title) → 3 filas · `MATCH 'ENG'` (identifier) → **0** filas | ✔ `identifier UNINDEXED` confirmado en runtime |
| `v_team_progress` / `v_issue.visible` | ✔ consultables, columnas `priority_label,is_open,visible` |
| `team_previous_key` y `schema_migration` aceptan fila con la tupla completa | ✔ |

D-009 y D-010 quedan **cerrados y verificados en el motor**, no sólo en prosa.

### 1.3 D-020 — Gherkin parseado con `gherkin-official 42.0.1`

```
archivos .feature: 17        Features parseados: 17        escenarios: 106
errores de parseo: 0         features sin @SPEC-001: 0
escenarios SIN When: 0       escenarios con >1 When: 0     escenarios sin tag @REQ-*: 0
sin @smoke: 0                con @slow: 12
Scenario: 99 · Scenario Outline: 7   (coincide con 001:696-697)
```

Cobertura: **37/37 REQ-F** con al menos un escenario; tags `@REQ-S001..S005` presentes. El mapeo
archivo→REQ de los 17 ficheros coincide **exactamente** con la tabla de 001:595-613 (verificado
comparando tags reales contra la tabla, no a ojo).

`# language:`: 0 ocurrencias. Un `Feature` por archivo: sí. **D-020 cerrado.**

### 1.4 Los 42 gaps

23 cerrados con evidencia (tabla en §2); 19 abiertos (§3). El patrón dominante: **los gaps que se
cerraron en la prosa de 001/010 NO se propagaron a `tasks.md` ni al Gherkin**, que son justamente
los artefactos que exigen AC verde para declarar "v1 terminado" (001:615-618).

---

## 2. Gaps del review 01 efectivamente cerrados (23)

| # | Evidencia en disco |
|---|---|
| 1 | 001:1-16 con los 12 campos (`component: soporte`, `version: 1`, `spec_revision: 3`, `implements:` 001:9-11) |
| 2 | 000 §6 (000:185-207): tabla `Error de dominio | Cuándo | CLI | HTTP | error.code` + fila `hifa doctor` (000:203) |
| 3 | 000:226-230: `When se ejecuta "hifa serve" sin flags` antes del `Then` (verificado por parser: 0 escenarios sin `When`) |
| 5 | 010:418-429 `CREATE TABLE team_previous_key` + `idx_team_previous_key_key`; R3 reescrita (010:454-458) |
| 6 | 010:245-247 CHECK con `date(due_date) IS due_date`; AC con `2026-13-45` y `2026-02-30` (010:562-567); **falla en sqlite** |
| 7 | 010:165-166 dos índices únicos parciales; R6 (010:472-476); **falla en sqlite** |
| 10 | 001 sin DDL (§3 remite a 010); `grep "CREATE TABLE" 001` → 0 |
| 11 | 010:377-399 FTS + 3 triggers; `identifier UNINDEXED` (010:379, verificado con `MATCH`); 001 EC-010/EC-007 alineados (001:468, 471) |
| 15 | 001 REQ-F037 (001:336-341) remite a SPEC-100 §6 (0/1/2/3) y "sale con 1/2 si hay hallazgos" |
| 21 | `# language` ausente; 0 errores de parseo con gherkin-official |
| 22 | 17 archivos, 17 `Feature`, un `Feature` por archivo (parseado) |
| 25 | 000 con §1..§10 (verificado por regex) |
| 26 | 000:233-243 tabla `Requisito → Verifica` para REQ-N/S |
| 27 | 010 con §2 Contrato público, §5 Flujo, §6 Errores (10 secciones) |
| 29 | 010:97-98 "Ningún archivo contiene `PRAGMA`"; R5.3 (010:468-470) |
| 30 | 010:270 y 010:208 FK compuesta `(workspace_id, team_id)`; probado: workspace inconsistente **falla** |
| 31 | DDL único: 001 no tiene constraints propias que diverjan |
| 32 | 001 REQ-N011 (001:399-404): `001_init.sql`/`002_`/`003_` + tupla completa del ledger |
| 33 | 001 REQ-F001 (001:115-120): "17 tablas + issue_fts, 3 views", `user_version = 3` |
| 34 | 001 REQ-F015 (001:196): "los triggers `AFTER DELETE` de `issue`", no cascada |
| 35 | 000 REQ-N001/N002 "manda el más exigente" (000:132-136) y 001 REQ-N002 "para la v1 manda este documento" (001:356-357): regla de precedencia explícita en ambos lados |
| 36 | 001 numerada §1..§10 (verificado por regex) |
| 42 | 000:4 `component: soporte` |

---

## 3. Gaps abiertos (19)

### Bloqueantes (13)

**Gap 4 — [acceptance/01-init.feature:13, :51] BLOQUEANTE**
El escenario de init sigue afirmando el estado de esquema viejo: `"schema_migration" tiene 1 fila
con version 1`. Contra el DDL vigente, tras `hifa db migrate` la tabla tiene **3 filas** (010:416,
010:546) y `user_version = 3`. El propio AC de 010 (010:540-547) exige 3 filas; el `.feature` de la
vertical dice 1. El gap se cerró en 010 §7 y **no** en el `.feature`, que era la mitad explícita del
hallazgo original.

**Gap 8 — [acceptance/02-teams-workflow-states.feature:41] y [tasks.md:91] BLOQUEANTE**
`And la issue "PLAT-1" tiene "ENG-1" en previous_identifiers` — campo inexistente en 010 (la
resolución de keys viejas es `team_previous_key`, 010:418-432). Repetido en tasks T-05:
`incluyendo previous_identifiers (aviso en stderr)`. 001 sí se corrigió (REQ-F006/F012/EC-010).

**Gap 9 — [tasks.md:86, :88] BLOQUEANTE**
T-05 sigue mandando `UPDATE team SET last_issue_number = last_issue_number + 1 ... RETURNING`
(columna inventada; 010 R2 dice "No existe contador en `team`", 010:451-453) y `SQLITE_BUSY`
(5 intentos) contra los **3** de D-018/010 R2/001 REQ-F011. Dos valores y una columna fantasma en la
misma task.

**Gap 12 — [tasks.md:222-224] BLOQUEANTE**
T-15 conserva el contrato viejo: `/api/issues` (sin `/api/v1`), `limit ≤ 100`, `DELETE` con
semántica previa. El dueño es 060:62/534-541 (`prefix="/api/v1"`, `limit` ≤ 200, `?hard` no existe,
D-015). El gap nombraba explícitamente "T-15 hereda el error": no se tocó.

**Gap 13 — [acceptance/15-cli-contract.feature:7-12, :18-20] y [tasks.md:211-213] BLOQUEANTE**
El `.feature` entero del contrato `--json` afirma el sobre que 001 §2 (001:65-69) y SPEC-050 §2.4
prohíben:
- `Scenario: el sobre de éxito tiene ok, data y meta` → `el campo "ok" es true`, `"meta.db"`,
  `"meta.duration_ms"`. El contrato vigente es objeto canónico sin envoltorio (050 §2.4.2) o
  `{items, next_cursor, has_more, total_estimate}` (050:423).
- `el campo "ok" es false` + `"error.hint"` → el error va en `stderr` como
  `{"error":{"code","message","field"}}` (050 §2.4.5, 000 §6).
- `error.code = "HIFA_E_NOT_FOUND"` → debe ser `not_found`.
El mismo sobre viejo aparece en 05-list-filters-order.feature:10,32 (`meta.count`),
10-search.feature:10,18,25,39, 12-history.feature:10. Y tasks T-14 (tasks.md:211-213) sigue
mandando `{"ok":true,"data":…,"meta":{count,db,duration_ms}}`.

**Gap 14 — [tasks.md:52, :212] BLOQUEANTE**
`core/errors.py: … DbError(5)` (el 5 es `invalid_transition` en D-014; base bloqueada/readonly es
`6`) y en T-14 "exit codes 0/1/2/3/4/5" (sin `6`). 001 §2/§6 sí quedaron en D-014.

**Gap 16 — [acceptance/10-search.feature:30, :10] y [tasks.md:173-175] BLOQUEANTE**
`When ejecuto "hifa search --reindex"` — flag que D-003/001:260/050:212 declaran inexistente (es
`hifa db reindex`). Además el escenario afirma `meta.count` en vez del sobre
`{hits,total,has_more,backend,took_ms}` (090:187). En tasks, T-11 vuelve a `--limit default 20`,
`hifa search --reindex` y `JOIN issue_view`.

**Gap 17 — [acceptance/16-dashboard.feature:10, :12] y [tasks.md:227] BLOQUEANTE**
Puerto **8420** en tres lugares (`http://127.0.0.1:8420/healthz`, "escuchando en el puerto 8420",
`--port 8420`), contra 7777 (000:230, 050 §2.5.1, 060, 100:66 `DEFAULT_PORT = 7777`).

**Gap 18 — [acceptance/17-dashboard-hardening.feature:17-20] y [tasks.md:228] BLOQUEANTE**
El escenario se llama "un host distinto de loopback **exige el flag explícito**" y espera
`exit code 2`. SPEC-000 REQ-S001 (000:151-153) y 001 REQ-S003 (001:422-426) dicen: `--host` explícito
+ warning en `stderr`, **exit 0** si el socket abre, sin flag de confirmación. tasks T-15 mantiene
`--i-understand-exposure (exit 2)`.

**Gap 19 — [acceptance/17-dashboard-hardening.feature:35-39] y [tasks.md:241-242] BLOQUEANTE**
`Scenario: una escritura htmx sin token devuelve 403` / `error.code = "HIFA_E_CSRF"`. SPEC-000
REQ-S005 (000:159-161) y 001 REQ-S005 (001:435-438): no hay token CSRF ni cookie `SameSite=Strict`;
el único guardia es `HX-Request` y sin él el fragmento responde **400**. tasks T-16 sigue pidiendo
"CSRF (cookie `SameSite=Strict` + `HX-Request` + `X-Hifa-Token`)".

**Gap 20 — [tasks.md:20-21] BLOQUEANTE**
T-01 crea `hifa/core/`, `hifa/db/`, `hifa/repos/`, `hifa/services/`, `hifa/views/`, `hifa/static/` —
exactamente el layout que el gap 20 marcó como violatorio del contrato de estructura de 000 §4.3.
El error se propaga a T-02..T-16 (`db/migrations/0001_init.sql` tasks:28, `db/connection.py`
tasks:31, `db/migrate.py` tasks:34, `core/ids.py` tasks:48, `services/bootstrap.py` tasks:68,
`views/` tasks:234). 001 §5.1 sí se corrigió (001:508-537): la spec y su plan de ejecución
describen dos árboles distintos.

**Gap 23 — [tasks.md completo] BLOQUEANTE**
Siguen sin task REQ-F006 (`team set-key`), REQ-F007 (`team archive`) y REQ-F024 (`label add/list/
archive` + grupos `singleSelect`). Verificado por script sobre las líneas `AC:` de las 17 tasks:
REQ-F declarados = 34 de 37; faltan exactamente **F006, F007, F024**. `grep "set-key|team archive|
label add"` en tasks.md → 0 coincidencias.

**Gap 24 — [tasks.md:4, :13] BLOQUEANTE**
`Acceptance criteria: specs/acceptance/hifa-v1.feature` (el directorio real es
`specs/acceptance/001-mvp-v1/`; el archivo suelto fue borrado en b664954) y
`[project.scripts] hifa = "hifa.cli:app"` contra `hifa.cli.main:app` (000:41, 000:247).

### Menores (6)

**Gap 28 — [INDEX.md:12] y [010:37-41 vs 030:217-266] MENOR (parcial)**
010 ya no reclama `migrate.py` en `implements:` (010:9-11) y §1 lo asigna a 030 (010:29-32), pero:
(a) `INDEX.md:12` sigue listando `migrate.py, connection.py` como gobierno de 010; (b) 010 §2 declara
como **normativo** el contrato de `migrate.py` (`open_db`, `schema_version`, `apply_migrations`,
`MigrationResult`) mientras 030 §2.3 declara otro para el mismo archivo (`migrate(db_path, *, now,
…)`, `current_version`, `pending_migrations`, `backup_database`). Sigue habiendo dos contratos
normativos para un módulo, sólo que ahora con dueño declarado. La nota de 010:78-80 reconoce el
`connect` vs `open_db` como deuda abierta.

**Gap 37 — [tasks.md:1-6] MENOR**
Sin frontmatter (`id`/`version`/`status`) y la regla de desempate sigue apuntando a un archivo
inexistente: "si `tasks.md` y el DDL de `spec.md` §Data Model divergen, gana el DDL" (tasks:6). No
menciona `010-data-model.md` ni D-008.

**Gap 38 — [tasks.md:14-16] MENOR**
T-01 instala `fastapi`, `uvicorn[standard]`, `jinja2` como dependencias **runtime**, contra A5 y
000 REQ-N007 (000:141-146: sólo en el extra `[web]`). La justificación de `markdown-it-py`/`nh3`/
`python-ulid` sí se agregó en 000.

**Gap 39 — [acceptance/*] MENOR**
11 pasos de acción dentro de bloques `Then` (fase de aserción ejecutando comandos):
`02-teams-workflow-states.feature` (1), `04-soft-delete.feature:13`, `05-list-filters-order.feature`
(2: :26, :33), `07-hierarchy-relations.feature` (2: :32, :33), `08-comments-attachments.feature`
(2: :12, :21), `11-cycles-projects.feature` (2: :31, :49), `16-dashboard.feature:91`.
(Se contaron sólo los `And` que empiezan con "ejecuto/lanzo" bajo `Then`.)

**Gap 40 — [acceptance/*] MENOR**
Assertions sobre texto frágil, sin cambios: `02:18` "stdout contiene el formato esperado de key",
`02:85` "stdout contiene \"3\"", `03:81` "stdout contiene \"campos válidos\"", `11:24` "stdout
menciona el ciclo 1 en conflicto", `17:20` "stderr contiene \"sin autenticación\"", `16:17` "stdout
contiene \"http://127.0.0.1:8500\"".

**Gap 41 — [acceptance/10-search.feature:21] MENOR (parcial)**
Tags resueltos (`@SPEC-001` en los 17 Features, `@smoke` en los 106 escenarios, `@slow` en 12) pero
el typo sigue: `Scenario: truncar y destrumbar restaura el resultado sin reindexar` (además el
escenario describe `trash`→`untrash`, no "truncar").

---

## 4. Inconsistencias nuevas o residuales (introducidas/visibles tras los fixes)

**N1 — [acceptance/01-init.feature:14, :16, :30, :35, :36] BLOQUEANTE**
El `.feature` siembra **5 estados** (posiciones 1.0–5.0) y **3 labels** (`Bug`, `Feature`,
`Improvement`). D-013 (DECISIONS:151-159), 001 REQ-F001 (001:115-120) y 050 §2.5.1 exigen
**6 estados** (`Triage`…`Canceled`, 1.0–6.0) y **4 labels** (+`Docs`). Al corregir 001 hacia D-013
no se actualizó su criterio de aceptación: el AC de la spec contradice la spec. Es el mismo patrón
que el gap 4 y afecta a 01-init (5 escenarios) y a 02 (`Given un team ENG con estados 1.0 a 5.0`).

**N2 — [acceptance/16-dashboard.feature:11, :81] BLOQUEANTE**
`el campo "schema_version" de la respuesta es 1` y `data.schema_version es 1`. El esquema vigente
deja `user_version = 3` (010:87-89, 010:543; 001 REQ-F001). Dos escenarios afirman un esquema que
el DDL no produce.

**N3 — [acceptance/16-dashboard.feature:20-25, :56, :64, :72] BLOQUEANTE**
Rutas que no existen en el dueño: `GET /api/issues?limit=100` con "límite máximo 100" (060:534:
`/api/v1/issues`, `limit` ≤ 200), `GET /partials/issue-rows` y `PATCH
/partials/issues/{id}/state` (070:52-92: `/{team}/board`, `/{team}/list`,
`/{team}/issue/{identifier}`, `/_/*`), `GET /issues/ENG-80` (070:54), `GET /api/palette?q=log`
(070:92: `/_/palette?q=`). Sumado a N1, el archivo `16-dashboard.feature` no es implementable.

**N4 — [acceptance/13-transfer-csv.feature:52-56] MENOR-BLOQUEANTE**
`Scenario: on-conflict skip es el comportamiento por defecto` sin pasar `--on-conflict`, y afirma
`data.skipped = 1`. 001 REQ-F030 (001:293-295) y 050:493 dicen default **`fail`**. Incoherencia
triple: 001 dice `fail`, tasks T-13 (tasks:202) dice `skip|update|error`, el `.feature` dice `skip`
por defecto.

**N5 — [acceptance/13-transfer-csv.feature:38] MENOR**
Fecha no ISO ⇒ `el exit code es 4`. El mapa D-014/000 §6 y 001 §6 (001:575) asignan `2`
(`Validation`/`validation_error`) a una fecha inválida. El 4 es `conflict`.

**N6 — [acceptance/14-transfer-json.feature:15] MENOR**
`el campo "schema_version" del dump es 1`, contra 001 REQ-F031 (001:298-301: `"schema_version":3`,
= `user_version`). tasks T-14 (tasks:208) también dice `schema_version: 1`.

**N7 — [001:311] MENOR**
`puerto default 7777 (el de SPEC-000 §5/SPEC-060/SPEC-100 §2.3)`: SPEC-000 **§5 es "Flujo"** y no
menciona el puerto; el 7777 vive en 000 §7 (000:230) y en REQ-S001 (000:151). Referencia a sección
rota introducida al reescribir 001.

**N8 — [001:704-708] MENOR (declarado, no resuelto)**
001 admite que los escenarios usan `issue add|update|trash|untrash|sub|tree|blocking|history`,
`team set-key`, `state add|delete`, `cycle add`, `milestone add`, `comment resolve|list|edit` y
`hifa info`, mientras el árbol normativo de 050 §2.5 usa `issue create|edit|delete|restore`,
`cycle create`, `project create`, `db path`, y **no tiene** `state *`, `milestone *` ni `team set-key`.
La nota dice "queda para el review de la ronda 2": este es ese review y sigue abierto. Consecuencia:
el Gherkin compila pero no es ejecutable contra el contrato de la CLI.

**N9 — [INDEX.md:11, :12, :57] MENOR**
`001 … Estado: draft` (001 frontmatter: `status: review`); `010 … gobierna migrate.py, connection.py`
(contradice 010:9-11/29-32); "Ya hay 7 decisiones registradas (D-001 … D-007)" con D-008..D-020 ya
publicadas.

---

## 5. Lo que sí quedó cerrado (para no re-abrir en la ronda 3)

- **El DDL es único, aplicable y correcto en el motor**: 17 tablas, 3 views, 3 triggers, FK
  compuestas activas, `due_date` con calendario real, unicidad de label por índices parciales,
  `identifier UNINDEXED`. Nada de esto requiere más cambios.
- **El Gherkin compila de verdad**: 17 ficheros = 17 Features, 106 escenarios, exactamente un `When`
  cada uno, 0 sin tag, cobertura 37/37 REQ-F. La parte estructural de D-020 está hecha.
- **000 quedó regenerable sola**: mapa de errores (§6), contrato público (§2), flujo (§5), tabla de
  cobertura REQ→spec (§7) y precedencia de presupuestos (§4.4).
- **010 pasó de 8 a 10 secciones** y su AC es consistente con su propio DDL (verificado ejecutando).
- **001 ya no define contratos**: §2 es una tabla de remisiones y §6 remite a 000/050/060/100/010.

---

## 6. Prioridad sugerida para la ronda 3

1. **Reescribir el Gherkin contra los contratos vigentes** (gaps 4, 8, 13, 16, 17, 18, 19, N1-N6).
   Es el bloque más grande y el que hoy hace que la suite no sea ejecutable: sobre `{ok,data,meta}`,
   `meta.count`, `HIFA_E_*`, `/api/issues`, `/partials/*`, 8420, `search --reindex`, `--hard`,
   `previous_identifiers`, 5 estados/3 labels, `schema_version: 1`. Un solo pase, archivo por
   archivo, contra 050/060/070/090/100 + D-011/D-013/D-014/D-015.
2. **Regenerar `tasks.md`** (gaps 9, 12, 14, 20, 23, 24, 37, 38, N4/N6): layout de 000 §4.3,
   `hifa.cli.main:app`, migraciones 001/002/003 con 17 tablas y `v_*`, `MAX(number)+1`, 3
   reintentos, exit codes D-014, `/api/v1`, 7777, sin CSRF, `error.code` de 050 §6, frontmatter, y
   las tres tasks faltantes (F006/F007/F024).
3. **Cerrar el seam 001↔050 de verbos** (N8): o 050 incorpora `state *`, `milestone *`,
   `team set-key` y los alias, o el Gherkin adopta los verbos de 050.
4. **Menores**: 39 (acciones en `Then`), 40 (assertions sobre texto), 41 (typo `destrumbar`),
   28 (`INDEX.md` + contrato de `migrate.py`), 35/37/38 y N7/N9.

---

## Anexo — verificación reproducible

```bash
cd /tmp && uv venv /tmp/rr1 --python 3.11 && uv pip install --python /tmp/rr1/bin/python gherkin-official
/tmp/rr1/bin/python /tmp/rr1/ddl_check.py        # 7 bloques SQL de 010 aplicados + 21 pruebas: PASS
/tmp/rr1/bin/python /tmp/rr1/extra_check.py      # FK compuesta workspace/team + CHECK de grupos
/tmp/rr1/bin/python /tmp/rr1/gherkin_check.py    # 17 Features / 106 escenarios / 0 errores / 1 When
/tmp/rr1/bin/python /tmp/rr1/coverage_check.py   # 37/37 REQ-F + mapeo archivo→REQ
/tmp/rr1/bin/python /tmp/rr1/quality_check.py    # 11 acciones en Then, 9 placeholders sin Examples
/tmp/rr1/bin/python /tmp/rr1/smoke_check.py      # @smoke 106/106, @slow 12
```

Hallazgo adicional de calidad no listado en los 42: 9 escenarios usan placeholders `<id>` /
`<id-vacio>` / `<state>` sin bloque `Examples` (`08-comments-attachments.feature` ×3,
`11-cycles-projects.feature` ×3, y usos en `16-dashboard`/`17-dashboard-hardening` con `<...>` en
paths), lo que deja steps no ejecutables tal como están escritos.
