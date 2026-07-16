# Architecture and Product Decisions

---

## D-001 — Universo inicial del MVP

**Fecha:** 2026-03-28

### Decisión

Empezar con un universo centrado en semiconductores / AI infra líquida.

### Motivo

- alta liquidez
- gran disponibilidad de datos
- fuerte cobertura mediática
- ideal para aprendizaje del sistema

### Consecuencia

El MVP inicial se centra en Layer 1.

---

## D-002 — Estrategia de universo por capas

**Fecha:** 2026-03-28

### Decisión

El sistema se diseñará para soportar 3 capas:

- Layer 1: large caps / core
- Layer 2: mid caps / cycle
- Layer 3: small caps / multibagger exploration

### Motivo

Separar objetivos:

- aprendizaje del sistema
- explotación de ineficiencias
- búsqueda de multibaggers

### Consecuencia

Layer 2 y 3 no forman parte del MVP inicial, pero son evolución planificada.

---

## D-003 — Uso de LLM local

**Fecha:** 2026-03-28

### Decisión

Usar LLM local en lugar de proveedor externo.

### Implementación

- Ollama
- Mistral

### Motivo

- evitar coste por tokens
- mayor control
- mejor aprendizaje del stack
- independencia de proveedor

### Evolución prevista

- evaluación de otros modelos
- posible uso de vLLM

---

## D-004 — Arquitectura por fases

**Fecha:** 2026-03-28

### Decisión

Construir el sistema en versiones progresivas:

- v0 → reglas simples
- v1 → scoring
- v2 → lógica probabilística
- futuro → memoria, critic, multi-agente

### Motivo

Evitar sobreingeniería y asegurar validación incremental.

---

## D-005 — No dockerizar agentes en fase inicial

**Fecha:** 2026-03-28

### Decisión

No separar cada agente en contenedores independientes en esta fase.

### Motivo

Los agentes actuales no son servicios autónomos reales.

### Estrategia

- ahora → ejecución local simple
- siguiente paso → contenedor único si es necesario
- futuro → separación por servicios si aporta valor

### Consecuencia

Se prioriza simplicidad y velocidad de desarrollo.

---

## D-006 — Gestión de entorno Python

**Fecha:** 2026-03-28

### Decisión

Usar `.venv` como entorno oficial del proyecto.

### Motivo

- evitar conflictos entre python global y entorno
- consistencia
- integración con direnv y VSCode

### Consecuencia

Se elimina uso de `python3` en aliases y se usa `python` dentro del entorno.

---

## D-007 — Estrategia de desarrollo

**Fecha:** 2026-03-28

### Decisión

Priorizar:

1. sistema funcional end-to-end
2. después sofisticación

### Motivo

- validar antes de optimizar
- evitar complejidad innecesaria
- mantener velocidad de iteración

### Consecuencia

Primero se cierra el loop completo (v0) antes de avanzar a arquitectura avanzada.
