# Spec Review 01 — hifa (SDD paso 2)

```
Verdict: FAIL
Specs revisados: 000-system-architecture.md, 010-data-model.md, 001-mvp-v1.md,
                 001-mvp-v1.tasks.md, acceptance/001-mvp-v1.feature
                 (referencia: README.md, _template.md, DECISIONS.md D-001..D-007/P-01..P-04, INDEX.md,
                  contraste con 020/030/050/060/070/090/100)
Gaps: 42 (bloqueantes: 24, menores: 18)
```

Revisor: read-only. No se editó ninguna spec, no se tocó git. Este es el único archivo escrito.

---

## Bloqueantes

### Frontmatter y estructura

**1. [SPEC-001 §frontmatter] BLOQUEANTE** — Faltan cuatro campos obligatorios del template:
`component`, `version`, `spec_revision`, `implements` (001:1-12 trae en su lugar `layer` y `tags`,
que no están en el template). README:53-54 exige los 12; INDEX:53 dice que "una spec sin
`implements:` válido no se considera aprobada" y sin `spec_revision` no hay drift check.
**Cambiar:** agregar `component`, `version: 1`, `spec_revision: 1` e `implements:` (globs del MVP o
la lista de specs que la vertical cubre). Si la vertical no gobierna código por diseño, eximirla
explícitamente con una decisión nueva (D-008) en `DECISIONS.md`.

**2. [SPEC-000 §2 A7 / §6] BLOQUEANTE** — A7 (000:43-44) dice que los errores de dominio se mapean
"en `cli/errors.py` y `api/errors.py`, **con la misma tabla**", pero SPEC-000 no contiene esa tabla en
ninguna sección. D-001 (DECISIONS:18) la invoca como si existiera ("excepción documentada al mapa
global de SPEC-000 A7"). Hoy el único mapa normativo vive en 050 §5.3 (050:889-898) y en la prosa de
D-001. Consecuencia: SPEC-000 no es regenerable sola y cualquier borde nuevo inventa sus códigos
(exactamente lo que pasó con 001, gap 14).
**Cambiar:** tabla normativa en SPEC-000 §6 con `Error de dominio | exit code | HTTP status`
(0 ok, 2 validación, 3 no encontrado, 4 conflicto, 5 transición inválida, 6 interno, 130 interrupción)
y la fila de excepción `doctor` (0/1/2/3) apuntando a D-001/SPEC-100.

**3. [SPEC-000 §7 Acceptance] BLOQUEANTE** — El escenario "serve escucha solo en loopback por defecto"
(000:119-122) no tiene `When` (`Given` → `Then` → `And`). Viola el criterio "todo Scenario con
Given/When/Then y siempre un When".
**Cambiar:** agregar `When se ejecuta "hifa serve" sin flags` antes del `Then`.

**4. [SPEC-010 §5 Acceptance] BLOQUEANTE** — El AC de la migración inicial afirma `user_version es 1`
(010:352) y "existen las 15 tablas" (010:353), pero §3 (010:41-44) dice que **todo** el bloque DDL se
aplica como `migrations/001_init.sql` e incluye `schema_migration` (010:267-272), que por D-006 es la
**migración 002**, y `team_previous_key` es la **003**. Tras `hifa db migrate` completo el estado real
es `user_version = 3` y 16 tablas (+ `issue_fts`). El AC no puede pasar contra el propio DDL.
El `.feature` repite el error (feature:13 "schema_migration tiene 1 fila con version 1").
**Cambiar:** fijar el estado final (`user_version = 3`, 16 tablas, 3 views) y separar el DDL por
archivo: `001_init.sql` (tablas/índices/views/FTS), `002_schema_migration.sql`, `003_team_previous_key.sql`.

### Modelo de datos (SPEC-010)

**5. [SPEC-010 §4 R3 / §3.1] BLOQUEANTE** — R3 (010:331-333) manda registrar el rename de key en
"`team.previous_keys` (tabla `team_previous_key(team_id, key, changed_at)` — migración 003)", pero:
(a) no existe `CREATE TABLE team_previous_key` en ningún bloque DDL — sin PK, sin FK, sin índice;
(b) `team.previous_keys` es una columna que tampoco existe (¿columna o tabla?);
(c) el almacenamiento de identifiers históricos que exigen 001 REQ-F006/REQ-F012
(`Issue.previousIdentifiers`) no tiene lugar en el modelo.
**Cambiar:** agregar el DDL completo en §3.1 como migración 003 (PK `(team_id, key)`, FK a `team`,
`changed_at`), o adoptar `issue.previous_identifiers TEXT NOT NULL DEFAULT '[]'` con `CHECK
json_valid(...)`; y registrar en `DECISIONS.md` cuál de las dos gana (afecta 001, 020 y 080).

**6. [SPEC-010 §4 R7 + §5] BLOQUEANTE** — R7 (010:341) afirma que una `due_date` inválida "es rechazada
por CHECK", y el AC (010:368-372) que `2026-13-45` "falla con Validation". El CHECK real (010:189) es
`GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'`, que **acepta** `2026-13-45`: valida la forma, no
el rango. La regla y el AC son falsos tal como están.
**Cambiar:** o `CHECK (date(due_date) = due_date)` (rechaza mes/día fuera de rango) o corregir R7 y el
AC para declarar el rechazo en dominio (`Validation`, capa app) en vez de en la base.

**7. [SPEC-010 §3.1 label / §4 R6] BLOQUEANTE** — R6 (010:339-340) sostiene que
`UNIQUE(workspace_id, team_id, name)` "permite el mismo nombre en ámbitos distintos" y garantiza la
unicidad dentro de cada ámbito. En SQLite `NULL != NULL`, así que con `team_id IS NULL` **dos labels de
workspace con el mismo nombre pasan** (010:109). 001 FIX-3 (001:642-644) documenta exactamente este bug;
010 lo reintroduce en el DDL normativo.
**Cambiar:** reemplazar ese UNIQUE por los dos índices únicos parciales
(`WHERE team_id IS NULL` / `WHERE team_id IS NOT NULL`) y ajustar R6 al mecanismo real.

### Modelo de datos (SPEC-001 vs SPEC-010)

**8. [SPEC-001 §REQ-F006 / §REQ-F012 / §Data Model 721 / feature 107,969] BLOQUEANTE** — `issue.previous_identifiers`
(JSON array) no existe en el DDL de 010: campo **inventado** (viola AGENTS §9 y D-005). 001 lo usa en
REQ-F006 (001:71-72), REQ-F012 (001:107) y EC-010 (001:969), y el `.feature` lo aserta (feature:107).
**Cambiar:** resolver junto con gap 5. Si gana la columna, agregarla a 010 §3 `issue` con tipo `TEXT` +
`CHECK json_valid`; si gana `team_previous_key`, reescribir REQ-F006/F012/EC-010 y el escenario.

**9. [SPEC-001 §REQ-F011 / §Data Model 609 / §REQ-N006 / tasks T-05] BLOQUEANTE** — `team.last_issue_number`
no existe en 010: campo **inventado**, y contradice 010 R2 (`MAX(number)+1` en la misma transacción,
010:329-330, ya implementado en 030 SQL-1 `next_number`, 030:322-327 y REQ-F036 en 030:700). También
divergen los reintentos: 010:330 "hasta 3"; 001:317-318 "hasta 5 veces" (repetido en EC-001, 001:960).
**Cambiar:** borrar el contador de 001 (REQ-F011, FIX-2, D11, §Generación del identifier), de T-05 y del
DDL de 001; o cambiarlo en 010/030 con decisión registrada. Unificar reintentos en un solo número.

**10. [SPEC-001 §Data Model 616-627, 668-688, 716-751] BLOQUEANTE** — El DDL de 001 no puede implementar
el de 010:
- `issue` sin `workspace_id` (010:180 `NOT NULL`) y `project` sin `workspace_id` (010:137 `NOT NULL`);
- `workflow_state` sin `UNIQUE (team_id, id)` (010:95), por lo que la FK compuesta de 010:212
  `FOREIGN KEY (team_id, state_id) REFERENCES workflow_state(team_id, id)` no se puede crear;
- `issue.state_id` con FK simple (001:729) → R4 ("state_id del mismo team", 010:334) queda sin
  enforcement en el esquema de 001.
**Cambiar:** 010 es la autoridad (D-005): corregir el bloque de 001 o —preferible— eliminar el DDL
consolidado de 001 y dejar sólo la remisión a SPEC-010 (001:19-21 ya lo declara normativo).

**11. [SPEC-001 §Data Model 819-843 y 852-884] BLOQUEANTE** — Dos divergencias de comportamiento:
- **FTS:** 001 indexa `identifier` (FIX-5, 001:898: "permite encontrar ENG-12") mientras 010 lo declara
  `UNINDEXED` (010:320) → buscar `ENG-12` deja de funcionar según qué spec se implemente. Además 010
  **no define los triggers** (sólo el comentario 010:323), así que 010 no es regenerable sola: el índice
  nunca se llena.
- **Vistas:** 001 crea `issue_view` y `team_open_counts` con columnas propias (`team_key`, `state_name`,
  `state_type`, `state_position`, `started_count`, `total_count`) y **no** crea `v_priority`; 010 define
  `v_priority`, `v_issue`, `v_team_progress` con otras columnas. Todo el resto del corpus usa
  `v_issue`/`v_priority` (020:358, 030:390, 060:358, 070:633, 090).
**Cambiar:** 010 manda. En 001: usar `v_issue`/`v_priority`/`v_team_progress` y quitar `issue_view`/
`team_open_counts` (001:492, 852-884, tasks T-02:30). En 010: agregar en §3.4 los tres triggers
`AFTER INSERT/UPDATE/DELETE` y decidir `identifier` indexado (recomendado: indexado, como FIX-5).

### Contratos de borde (SPEC-001)

**12. [SPEC-001 §Design "Contrato de la API REST" 494-514 / §"Superficie de comandos CLI" 516-557] BLOQUEANTE**
— D-005 dice que 001 **no define contratos**, pero define árbol de comandos y tabla de endpoints, y
divergen de los dueños:
| 001 | dueño |
|---|---|
| `/api/issues`, `/api/teams`, … (001:252-259, 501-514) | `060:62` registro con `prefix="/api/v1"`, 060:525-611 |
| vista de lista `GET /` (001:512) | `070:52` `GET /{team}/list` |
| palette `GET /api/palette` (001:508) | `070:91` `GET /_/palette` |
| `DELETE /api/issues/{id}` = trash (001:505) | `060:529` soft con `?hard` |
**Cambiar:** reemplazar ambas tablas por remisiones a SPEC-050/060/070 (es lo que D-005 manda) o
alinearlas 1:1 contra ellos. T-15 (tasks:220-222) hereda el error.

**13. [SPEC-001 §REQ-F032 238-244 / feature 721-751] BLOQUEANTE** — Tres incompatibilidades con el contrato
`--json` de 050:
- sobre universal `{"ok":true,"data":…,"meta":{"count","db","duration_ms"}}` (001:239) vs. listas
  `{items, next_cursor, has_more, total_estimate}` (050:366-377);
- errores en **stdout** con exit ≠ 0 (001:240-241) vs. `--json` = error en **stderr** y `stdout` vacío
  (050:396-400);
- códigos `HIFA_E_NOT_FOUND` / `HIFA_E_DEPTH` / `HIFA_E_CSRF` (001:240, 161, 381) vs. `not_found` /
  `validation_error` / `conflict` (050:905-920, 060:932).
Los **106 escenarios** del `.feature` asertan `meta.count` y `error.code` → fallarían todos contra el
contrato de 050.
**Cambiar:** 001 adopta el sobre, el canal y los códigos de 050 (y deja `meta.*` sólo como dato de
`doctor`); el `.feature` se reescribe en consecuencia.

**14. [SPEC-001 §REQ-F032 242-244] BLOQUEANTE** — Mapa de exit codes propio: `0` ok, `1` genérico,
`2` uso/validación, `3` no encontrado, `4` conflicto, **`5` error de base de datos**, sin `6`.
D-001 y 050 §5.3 fijan `5` = transición inválida y `6` = interno (050:892-897); `5` en 001 significa
otra cosa (DB locked/readonly/corrupt). Reaparece en T-03 (tasks:52 `DbError(5)`) y en EC-008/EC-009
(001:967-968).
**Cambiar:** 001 referencia 050 §5.3 sin redefinir (0/2/3/4/5/6, 130) y mueve "base bloqueada/readonly/
corrupta" a `6` (o a `3` para `DatabaseNotFound`, como 050:914).

**15. [SPEC-001 §REQ-F037 273-278] BLOQUEANTE** — "sin flags es read-only y sale con exit code `0`
aunque encuentre problemas" contradice D-001 (`doctor` = 0 ok / 1 warn / 2 fail / 3 no diagnosticable)
y SPEC-100 §6. El AC (feature:828-835) refuerza el error.
**Cambiar:** 001 remite a SPEC-100 §6 y no redefine los exit codes de `doctor`; el escenario debe
esperar 1 o 2 cuando hay findings.

**16. [SPEC-001 §REQ-F025 189-193 / tasks T-11:172 / feature 558-562] BLOQUEANTE** — `hifa search`
"devuelve como máximo 20 resultados (default)" contradice D-002 (default **50**, rango **1-200**,
090:187 y 090:715-716), y `hifa search --reindex` contradice D-003 (el rebuild es **`hifa db reindex`**,
única acción correctiva; 090:379, 090:708, 100:41, 050:517). La spec tampoco menciona `backend` ni
`took_ms`, obligatorios por D-002.
**Cambiar:** default 50 / rango 1-200 / sobre `{hits,total,has_more,backend,took_ms}`; reemplazar
`--reindex` por `hifa db reindex` en 001:192-193, T-11:173 y feature:560, y ajustar el escenario
(feature:558-562) a `hifa db reindex`.

**17. [SPEC-001 §REQ-F033 246-247 y §REQ-S003 364 / tasks T-15:225 / feature 758-768] BLOQUEANTE** —
Puerto por defecto **8420** en 001 vs **7777** en SPEC-000 §7 (000:122), 050:415 y 060:701/706. Un
contrato con dos valores por defecto no es implementable.
**Cambiar:** unificar en 7777 (000 + 050 + 060 son mayoría y 000 es la spec de arquitectura); corregir
001:247, 001:364, T-15 y feature:761/763/768.

**18. [SPEC-001 §REQ-S003 364-368 / feature 858-862] BLOQUEANTE** — 001 exige `--i-understand-exposure`
y **exit 2** sin él; 000 REQ-S001 (000:93-94) sólo pide `--host` explícito + warning, y 050:415 ofrece
`--read-only` sin flag de ack. No hay decisión que resuelva el conflicto (D-005 dice que manda el
componente, pero 000 y 050 tampoco coinciden entre sí).
**Cambiar:** registrar D-008 en `DECISIONS.md` con el comportamiento único (flag, mensaje, exit code) y
alinear 000/001/050/feature.

**19. [SPEC-001 §REQ-S005 378-383 / feature 877-881] BLOQUEANTE** — 001 define CSRF con `X-Hifa-Token` +
cookie `SameSite=Strict` + `403 HIFA_E_CSRF`, y además un `Bearer` leído de `~/.config/hifa/token`;
070 no tiene CSRF y responde **400** si falta `HX-Request` (070:99, 070:736), 060:1185 dice
"CORS, cookies, CSRF: no hay cliente navegador", y 000 REQ-S005 (000:99-100) dice que `serve` no
implementa auth en v1. Tres contratos incompatibles sobre el mismo borde.
**Cambiar:** decidir en `DECISIONS.md` (¿CSRF sí/no?, ¿400 o 403?, ¿Bearer?) y reflejarlo en 070/060/001.
Mientras no haya decisión, 001 no puede exigir un header que 070 rechaza.

### Arquitectura (SPEC-000 A1/A3 y contrato de estructura)

**20. [SPEC-001 §Design 409-450 / tasks §T-01:20-21] BLOQUEANTE** — El layout de paquetes
`hifa/core/`, `hifa/db/`, `hifa/repos/`, `hifa/services/`, `hifa/views/`, `hifa/static/` contradice el
**contrato de estructura** de 000 §4 (000:64-74: `domain/`, `ports.py`, `app/`, `adapters/sqlite/`,
`cli/`, `api/`, `web/`, `config.py`) y las reglas A1/A3 (el diagrama de 001:434-443 pone la SQL en
"REPOSITORIES" y los servicios por encima, sin `ports.py`). Cambiar la estructura es cambio de
`version` de SPEC-000 (000:76). Las specs 020-100 ya usan el layout de 000.
**Cambiar:** 001 §Design y T-01 usan `domain/ + ports.py + app/ + adapters/sqlite/ + cli/ + api/ + web/
+ config.py`; T-01 debe crear además `config.py` y `ports.py`.

### Acceptance (Gherkin)

**21. [acceptance/001-mvp-v1.feature:1] BLOQUEANTE** — El archivo declara `# language: es` pero usa
keywords en inglés (`Feature:`, `Scenario:`, `Given/When/Then/And`, `Examples:`). Verificado con
`gherkin-official 42.0.1`: **11 errores de parseo** ("expected: #TagLine, #FeatureLine, #Comment,
#Empty, got 'Feature: Inicialización…'"). El archivo no compila hoy.
**Cambiar:** `# language: en` (el texto de los steps puede seguir en español) o traducir las keywords a
`Característica/Escenario/Dado/Cuando/Entonces/Y/Ejemplos`. Verificar en CI con un parse real.

**22. [acceptance/001-mvp-v1.feature] BLOQUEANTE** — 17 bloques `Feature:` en un solo archivo. Aun
quitando el header de idioma, el parser falla en cada `Feature:` posterior (líneas 67, 180, 281, 326,
377, …): un archivo = un Feature en Gherkin.
**Cambiar:** partir en un `.feature` por área (init, teams/states, issues, board, search, cycles,
transfer, api/web, hardening) — lo que además resuelve P-01 para el resto de componentes — o anidar con
`Rule:` bajo un único Feature.

### Tasks

**23. [tasks.md §T-04/T-05/T-06] BLOQUEANTE** — REQ-F006 (`team set-key`), REQ-F007 (`team archive`) y
REQ-F024 (labels + grupos `singleSelect`) no aparecen en ninguna task (verificado: 34/37 REQ-F
asignados). Son requisitos con AC verde exigido por la definición de "v1 terminado" (001:1032-1035).
**Cambiar:** agregar tasks (o subtareas) para `set-key` (incluye reescritura de identifier + historia),
`archive/unarchive` (bloqueo por issues abiertas) y `label add|list|archive` + grupos singleSelect.

**24. [tasks.md:4 y :13] BLOQUEANTE** — Dos referencias rotas: `Acceptance criteria:
specs/acceptance/hifa-v1.feature` (el archivo real es `specs/acceptance/001-mvp-v1.feature`), y
`[project.scripts] hifa = "hifa.cli:app"` que contradice 000 §8 (`hifa = "hifa.cli.main:app"`, 000:127).
**Cambiar:** corregir ambas rutas/firmas.

---

## Menores

**25. [SPEC-000 §secciones] MENOR** — De las 10 secciones del template faltan: **§2 Contrato público**
(parcialmente sustituido por §4 estructura, sin firmas), **§3 Datos que toca** (N/A en arquitectura) y
**§5 Flujo**. §6 Errores está ausente como catálogo (ver gap 2). **Cambiar:** agregarlas o declarar
"N/A — se cubre en SPEC-0NN" para que el checklist del template cierre sin ambigüedad.

**26. [SPEC-000 §7] MENOR** — Cobertura de AC: 3 escenarios cubren A2, A4/A5 y REQ-S001. REQ-N001..N008
y REQ-S002..S005 (normativos) no tienen escenario ni referencia a la spec que los verifica.
**Cambiar:** tabla `REQ → spec/escenario que lo verifica` o un escenario por REQ-N/S.

**27. [SPEC-010 §secciones] MENOR (= P-04)** — Verificado: 8 secciones. Faltan **Contrato público**,
**Flujo** y **Errores**. Hoy el contrato de `migrate.py`/`connection.py` (que 010 gobierna) vive en
SPEC-030, y los errores de migración (checksum alterado, FTS5 ausente) están dispersos en 050/100.
**Cambiar:** agregar las 3 secciones, o declarar en §1 que el contrato de esas funciones pertenece a
SPEC-030 y remitir el catálogo de errores a 050 §6.

**28. [SPEC-010 §frontmatter implements + INDEX:14] MENOR** — 010 declara `migrate.py` y `connection.py`
y 030 (INDEX:14) también los gobierna → doble dueño, drift ambiguo (`spec_contract_check` no sabría a
qué spec culpar). **Cambiar:** dejar esos dos archivos en un solo spec (030) y que 010 gobierne sólo
`migrations/**`.

**29. [SPEC-010 §3 cabecera 46-49 / §6.3] MENOR** — §3 incluye `PRAGMA journal_mode = WAL` dentro del
`.sql` de migración, mientras §6.3 (010:384) dice que `connection.py` los aplica en cada conexión.
`journal_mode` no se puede cambiar dentro de una transacción → la migración fallaría si se ejecuta en
`BEGIN`. **Cambiar:** sacar los PRAGMA del `.sql` y dejarlos sólo en `connection.py`.

**30. [SPEC-010 §3.1 issue.workspace_id] MENOR** — `issue.workspace_id` (010:180) está denormalizado
respecto de `team.workspace_id` sin constraint que los ate: pueden divergir (y `project.workspace_id`
tampoco se valida contra `project.team_id`). **Cambiar:** FK compuesta `(workspace_id, team_id) →
team(workspace_id, id)` o derivarlo por join en las views.

**31. [SPEC-001 §Data Model — constraints] MENOR** — Diff cosmético de constraints entre 001 y 010:
001 agrega CHECK de `team.key` (001:591), `number > 0` (719), `json_valid(metadata)` (800), GLOB en
`start_date`/`target_date` (680-683), `ON DELETE CASCADE` en `label.team_id/parent_id` (632-633) y
`comment.parent_id` (783), y un set de índices distinto (001:753-760 vs 010:278-289). Todo eso hace que
el drift check de tablas/columnas/índices falle entre las dos specs.
**Cambiar:** mover a 010 las diferencias que valgan la pena (010 es la autoridad) y eliminar el DDL de
001 para que exista una sola fuente.

**32. [SPEC-001 §REQ-N011 340-345] MENOR** — Nombra las migraciones `0001_init.sql` (4 dígitos) mientras
010 usa `migrations/001_init.sql` (010:43); y enumera `schema_migration(version, applied_at, checksum)`
omitiendo `name` (010:267-272, D-006). **Cambiar:** `001_init.sql` y la tupla completa.

**33. [SPEC-001 §REQ-F001 41-42] MENOR** — "las 2 vistas derivadas" vs 3 views en 010
(`v_priority`, `v_issue`, `v_team_progress`), y da por hecho que el ledger es de la migración inicial
(ver gap 4). **Cambiar:** "3 vistas" y remitir el estado del ledger a 010.

**34. [SPEC-001 §REQ-F015 128-129] MENOR** — Lista `issue_fts` entre las cascadas de `delete --hard`.
El FTS es external-content y se mantiene por trigger `AFTER DELETE`, no por FK; "cascada" describe mal
el mecanismo. **Cambiar:** "los triggers `AFTER DELETE` de `issue` limpian `issue_fts`".

**35. [SPEC-001 §REQ-N002 vs SPEC-000 REQ-N001/N002] MENOR** — Umbrales distintos para la misma métrica:
RSS de CLI 80 MB (001:293) vs 120 MB (000:81); `serve` 120 MB (001:292) vs 150 MB (000:82); arranque
250 ms p50 (000:84) vs 250 ms sólo en frío (001:288-289). Ninguna dice cuál manda.
**Cambiar:** unificar, o declarar en 001 "más estricto que SPEC-000; manda este".

**36. [SPEC-001 §estructura] MENOR** — No sigue el template: secciones sin numerar (`Purpose`,
`Requirements`, `Design`, `Data Model`, `Edge Cases`, `Open Questions`, `Notes`), sin §7 Acceptance
Criteria inline (sólo remisión al `.feature`), sin §8 Regeneration checklist (lo asume `tasks.md`) y sin
§6 Errores; `Provenance` y `Out of scope` quedan embebidos en Notes (001:1012-1031).
**Cambiar:** numerar 1-10, o declarar el esqueleto alterno de la vertical en `DECISIONS.md` (D-008).

**37. [tasks.md] MENOR** — Sin frontmatter (ni `id`, ni versión, ni `status`), y la regla de desempate
"si `tasks.md` y el DDL de `spec.md` §Data Model divergen, gana el DDL" (tasks:6) apunta a un `spec.md`
que no existe (hoy serían `001-mvp-v1.md` y `010-data-model.md`, y por D-005 manda **010**).
**Cambiar:** agregar frontmatter mínimo y corregir la referencia al archivo y a la autoridad (010).

**38. [tasks.md §T-01:14-16] MENOR** — Agrega dependencias no previstas en 000 REQ-N007
("dependencias de la CLI: `typer` + `pydantic` únicamente", 000:88): `markdown-it-py`, `nh3`,
`python-ulid`, y `fastapi`/`uvicorn`/`jinja2` como runtime (A5 las quiere en el extra `[web]`).
**Cambiar:** justificar `nh3`/`markdown-it-py`/ULID en 000 (bump de version) y mover
`fastapi`/`uvicorn` al extra `web`.

**39. [feature] MENOR** — Acciones dentro del bloque `Then` (anti-patrón: la fase de aserción ejecuta
comandos): 129-131, 292, 351, 449-450, 474, 483, 607, 625, 842. Hacen los escenarios no atómicos y
dependientes del orden. **Cambiar:** mover cada acción al `When`/`And` de acción y dejar en `Then` sólo
afirmaciones de estado.

**40. [feature] MENOR** — Assertions sobre texto frágil en vez de estructura: `stdout contiene "campos
válidos"` (260), `stdout contiene el formato esperado de key` (84), `stdout contiene "3"` (151),
`stdout menciona el ciclo 1 en conflicto` (600), `stderr contiene "sin autenticación"` (862).
**Cambiar:** aseverar `error.code` / `error.field` / `data.*` (cuando el contrato de 050 defina el
sobre, gap 13).

**41. [feature] MENOR** — Sin tag de spec (`@SPEC-001`) a nivel Feature — el template lo exige
(`_template.md:85`), y sin tags de ejecución (`@smoke`/`@slow`) pese a que el archivo mezcla escenarios
de concurrencia (208-214) con unitarios. Además, typo en el nombre del escenario de la línea 551
("truncar y destrumbar"). **Cambiar:** `@SPEC-001` arriba del Feature y tags de selección; corregir el
título (debe decir `trash`/`untrash`).

**42. [SPEC-000 §frontmatter] MENOR** — `component: sistema` (000:4) no pertenece al enum del template
(`dominio | persistencia | aplicacion | entrada | soporte`, `_template.md:4`).
**Cambiar:** agregar `sistema`/`vertical` al enum en `_template.md` o usar `soporte`.

---

## Anexo A — Diff `SPEC-001 §Data Model` vs `SPEC-010 §3` (fidelidad campo por campo)

Revisé uno por uno los campos citados en 001 y 000 contra el DDL de 010. Resultado:

| Elemento | 010 (autoridad) | 001 | Discrepancia |
|---|---|---|---|
| `team.last_issue_number` | no existe | 001:609 | **inventado** (gap 9) |
| `issue.previous_identifiers` | no existe | 001:721 | **inventado** (gap 8) |
| `issue.workspace_id` | `TEXT NOT NULL` (180) | ausente | gap 10 |
| `project.workspace_id` | `TEXT NOT NULL` (137) | ausente | gap 10 |
| `workflow_state UNIQUE(team_id,id)` | sí (95) | ausente | gap 10 (FK compuesta imposible) |
| `issue.state_id` FK | compuesta `(team_id,state_id)` (212) | simple (729) | gap 10 / R4 |
| `label` unicidad | `UNIQUE(workspace_id,team_id,name)` (109) | 2 índices parciales (643-644) | gap 7 (010 roto) |
| `issue_fts.identifier` | `UNINDEXED` (320) | indexado (819-826) | gap 11 |
| triggers FTS | sólo comentario (323) | 3 triggers (828-843) | gap 11 |
| views | `v_priority`, `v_issue`, `v_team_progress` | `issue_view`, `team_open_counts` | gap 11 |
| `schema_migration` | migración 002 | dentro de `0001_init.sql` (574) | gaps 4, 33 |
| `team_previous_key` | migración 003, sin DDL | no existe | gap 5 |
| CHECKs/índices | ver 010:189, 278-289 | ver 001:591, 680-683, 753-760 | gap 31 |
| `priority` 0-4, `estimate`, `due_date`, `sort_order`, `board_order`, `sub_issue_sort_order`, `trashed`, `archived_at`, `canceled_at`, `started_at`, `completed_at`, `snoozed_until_at`, `branch_name`, `priority_sort_order` | ✓ | ✓ | **correctos** (nombre y tipo) |
| `label.is_group/group_type/parent_id/retired_at`, `comment.parent_id/resolved_at/edited_at`, `attachment.url/metadata/source_type`, `cycle.number/starts_at/ends_at`, `project.status_type/priority/lead_id/start_date/target_date`, `project_milestone.status`, `issue_history.actor_id/field/old_value/new_value` | ✓ | ✓ | **correctos** (nombre y tipo) |

Enums citados en 001 y válidos según 010: `state.type` (6 valores), `issue_relation.type`
(`blocks|duplicate|related|similar`), `project.status_type` (6), `project_milestone.status` (4),
`team.visibility` (3), `issue_estimation_type` (5). Sin enum inventado.

---

## Verificaciones que PASARON

- **Cobertura Gherkin:** los **37 REQ-F** tienen al menos un escenario con tag `@REQ-F0NN`
  (verificado por script: 0 huérfanos) y hay **106 escenarios** en el `.feature`, que coincide con lo
  que declara D-005. 0 escenarios sin tag, 0 escenarios sin `When`.
- **Frontmatter 000 y 010:** los 12 campos presentes y coherentes con `INDEX.md` (`approved`);
  `implements:` con globs/rutas válidas y sin rutas inexistentes absurdas (el código aún no existe: P-03).
- **Secciones 010:** efectivamente 8 (P-04 confirmado, no hay más recortes).
- **Fidelidad de campos:** de los ~50 campos citados en 001/000, sólo 2 son inventados
  (`previous_identifiers`, `last_issue_number`) más los nombres de views y el flag FTS (Anexo A).
- **Framing moralizante:** ninguno. Barrido de `legítimo|fair use|antiético|ético|respetar términos|
  moral` sobre los 5 archivos (y DECISIONS/README/INDEX): sin coincidencias reales.
- **A1-A8:** ninguna spec de componente hace que `domain/` importe infra ni que `app/` escriba SQL o
  formatee salida (000 A1-A8 son internamente coherentes); el único problema arquitectónico es el
  layout de 001/tasks (gap 20).
- **D-002, D-003, D-006:** correctamente aplicadas en 050/090/100/010; las violaciones están sólo en
  001/tasks/feature (gaps 4, 16).

---

## Observaciones NO computadas (deuda ya registrada en `DECISIONS.md`)

- **P-01** — `SPEC-000:13` y `SPEC-010:13` declaran `acceptance: specs/acceptance/0NN-*.feature`, que
  no existen (sólo existe `001-mvp-v1.feature`). Es deuda P-01, ya documentada (DECISIONS:85) y
  **no se cuenta como gap** por instrucción del encargo. Se resolvería de paso con el gap 22 (partir el
  `.feature` por área).
- **P-02** — IDs planos en 020/030/040: fuera de alcance de esta review (no se revisaron esos archivos).
- **P-03** — `scripts/spec_contract_check.py` no existe: esperado en esta fase (primer task de
  implementación).
- **P-04** — Secciones faltantes de 010: listado una vez en gap 27, con la nota de que ya está en deuda.

---

## Prioridad sugerida para el Fix Agent

1. **Gaps 21, 22** (el `.feature` no parsea: nada de lo demás se puede verificar hasta que compile).
2. **Gaps 5, 8, 9, 10, 11, 31** (una sola fuente de verdad del esquema: decidir en DECISIONS y borrar
   el DDL paralelo de 001).
3. **Gaps 4, 6, 7** (AC y reglas de 010 que no se sostienen contra su propio DDL).
4. **Gaps 12-19** (contratos de borde de 001: remitir a 050/060/070 y no redefinir).
5. **Gaps 1, 2, 3, 20, 23, 24** (frontmatter, mapa de errores de 000, AC sin `When`, layout, tasks
   faltantes).
6. Menores (25-42).
