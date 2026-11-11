## Resultado: no pude escribir el archivo — ver nota al final

Agoté el límite de iteraciones justo antes de escribir `specs/reviews/spec-review-02-r2.md`. **El reporte NO existe en disco**; todo lo verificado está abajo. Nada más se modificó (no toqué specs ni git).

**Verdict: FAIL** — 5 de 9 bloqueantes cerrados; 4 abiertos + 5 bloqueantes nuevos.

### Bloqueantes del review 02

| # | Gap | Estado | Evidencia |
|---|---|---|---|
| 1 | Retry fuera del `@contextmanager` (D-018) | **ABIERTO** | `030:170-178` dice "sin reintento interno", pero `030:935-952` (§4.4) sigue con `for attempt in 1..policy.attempts: BEGIN IMMEDIATE / yield repos / COMMIT` dentro de `write()`; `030:839-842` (R4) y `030:877` (REQ-F030-06) prometen "rehaciendo la transacción completa" y `030:1084-1092` lo testea |
| 2 | `uow.clock`/`uow.idgen` | CERRADO | `030:164-169` properties + §2.2 ítem 6 (`030:210-212`) |
| 3 | Canal `readonly` en repos | CERRADO | `030:270-274` (`readonly: bool = False`), `030:200-203`, R6 `030:845-846`, AC `030:1070-1075` |
| 4 | `IssueView.visible` (D-019) | CERRADO | `020:360-370`. Reproducido con pydantic 2.13.4: `warnings: ninguno`, `iv.visible=False`, `iv.is_visible=True` |
| 5 | `positions(state_id=...)` | CERRADO | `030:314`; consumido en `040:967-968` y `040:1027` |
| 6 | Una sola estrategia de `next_number` | **ABIERTO** | `030:392-416` pone el retry en `create_issue` de 040, pero `040:1541` dice "el único reintento es el de SQLITE_BUSY, y vive en SPEC-030"; `040:962` ("reintenta UNIQUE") no tiene bucle ni REQ; `040:1172/1183` deja `DUPLICATE_NUMBER` como resultado posible |
| 7 | `set_state` sin regla de hitos | **ABIERTO (roto en 040)** | 030 correcto (`030:312`: solo `state_id`+`updated_at`), pero `040:1050` llama `set_state(issue.id, target.id, milestones=hitos, now=now)` — kwarg inexistente en la firma |
| 8 | Puerto de 040 completo | **PARCIAL** | 55 `def`, 0 duplicados; pero bordes aún usan `get_issue` (`050:199`, `070:128`), `get_board` (`070:126,783`), `relative_to=` (`050:189,869`), `SetMemberActive` (`050:239`) — ninguno existe en 040 |
| 9 | `ports.py` en 030 con `Clock`/`IdGen` | CERRADO | `030:9-13` (`src/hifa/ports.py` en `implements`), §2.7 `030:757-799` |

### Bloqueantes nuevos

1. **`030:463` y `030:532` usan `i.is_visible`** — `v_issue` expone `visible` (`010:355-362`). El SQL no ejecuta.
2. **`040:1086-1089` llama `list_view` con 13 kwargs** (`team_id=`, `after=`, `q=None`…) pero `030:741` declara `list_view(conn, filters: ListFilters)`. Contrato incompatible.
3. **5 símbolos de dominio inexistentes**, invocados por 040 y ausentes del contrato de 020: `rules.apply_milestones` (`040:1049`), `rules.slugify` (`040:728,869`), `rules.branch_name` (`040:965`), `ranking.exhausted` (`040:1029`), `validate_attachment_url` (`040:445`). R17 delega `slugify` en 020 y 020 §10 la delega en 040 → circular.
4. **`InvalidTransition` sí se produce en 040** (`040:1135`, `040:1184`, `040:466/478`) mientras `020:575` dice "nunca lanza" y `020:849` la declara reservada sin productor. Contradicción de contrato.
5. **3 filas de `030:1020-1023` mapean a exit `1`** (DB_NOT_MIGRATED, DB_CORRUPT, SQLITE_TOO_OLD) — D-014 reserva `1` a `hifa doctor`.

### Verificaciones que PASAN (evidencia)

- **Modelos ↔ DDL**: 16 modelos, `FALTANTES=0 SOBRANTES=0` (Issue 31/31, Team 20/20, Project 16/16, Member 11/11, Label 10/10, Comment 9/9, WorkflowState 8/8, Attachment 8/8, Cycle 7/7, IssueHistory 7/7, ProjectMilestone 6/6, IssueRelation 5/5, Workspace 4/4, TeamMembership 4/4, TeamPreviousKey 3/3, IssueView = Issue+3). `issue_label` (M2M) y `schema_migration` justificados.
- **Pureza 020**: 0 imports de `sqlite3/fastapi/typer/click/jinja2/httpx/os/time/random/uuid/ulid/hifa.ports/hifa.adapters/hifa.app`; únicas coincidencias de `datetime.now(`/`utcnow(`/`time.time(` en `020:32`, prosa prohibitiva.
- **Pureza 030**: 0 `validate_*`, 0 `ranking.`, 0 `apply_milestones`, 0 `triage_enabled`.
- **Pureza 040**: 0 literales SQL en bloques python, 0 `print(`, 0 fastapi/httpx/APIRouter.
- **Cobertura del puerto**: 77 identificadores `fn(` en 050/060/070; 19 con `def` en 040; 40 justificados (§2.3 + stdlib/FastAPI/Typer); residuo real = `get_issue`/`get_board` (drift de borde ya conocido).
- **IDs**: 0 duplicados intra-spec, 0 colisiones entre spec; formato canónico `REQ-F020-NN`, 020 19/19 con escenario, 030 22/22.
- **Gherkin `>1 When`**: 020 = 0 (gap 27 cerrado); 030 = 6, 040 = 7 (sin cerrar).

### Otros gaps sin cerrar / nuevos (menores)

- `030:1244` — `# 15 tablas + issue_fts` (gap 18 sin cerrar; son 17).
- `030:881` (REQ-F030-10) y `030:1290` — "`limit` máx **500**" contra D-011 (1-200) y contra su propio `__post_init__` (`030:637`).
- `030:811` — invariante de `label` cita `UNIQUE(workspace_id, team_id, name)`, reemplazado por D-010.
- `030:175` — referencia `SPEC-040 §2.4`, sección inexistente; `app/retry.py::with_write_retry` no está declarado en 040.
- `040:974-975` — `cmd.sort_order or ranking.last(...)` (gap 21 sin arreglar; `0.0` es falsy).
- `040:946` — usa `ws.id` sin obtenerlo (gap 20 sin arreglar).
- `040:1162` — `COMMENT_DELETED` cita `comment.deleted_at`, columna ausente en `010` (0 ocurrencias).
- `040:1171` — `UNIQUE_TEAM_KEY` ausente de la tabla §6.
- `040:483` — `RebalanceReport` nunca definido.
- `040:1003` — "read models sin referencias a `sqlite3`" pero §2.6 tipa `sqlite3.Connection`.
- `030:122-128` — bloque de `uow.py` no importa `sqlite3` ni `Path`, usados en `030:159`.
- `030` y `040` declaran cada uno una `IssuePage` distinta (misma forma, dos dueños).
- `REQ-F040-26` y `REQ-F040-28` sin escenario.

**Falta:** escribir todo esto en `~/specs/reviews/spec-review-02-r2.md` (único artefacto permitido, todavía no creado).