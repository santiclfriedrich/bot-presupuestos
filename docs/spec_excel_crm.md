# Especificación: carga de presupuestos desde Excel (CRM → bot GBP)

El CRM recibe el Excel, lo valida, lo parte en jobs de hasta 20 ítems y los encola
en `/api/v1/gbp-bot/jobs`. El bot no recibe el archivo: sólo consume jobs.
Cada job se convierte en **un presupuesto en GBP**, con su número y su PDF.

## 1. Archivo de entrada

- Formato `.xlsx`. Se lee la primera hoja (hoy se llama "Detalle Items").
- La fila 1 es el encabezado. Las columnas se ubican **por nombre**, nunca por
  posición. Para comparar nombres: sin espacios al inicio ni al final, sin
  distinguir mayúsculas ni tildes.
- Se ignoran las filas totalmente vacías.

| Columna           | Obligatoria | Uso                                                              |
|-------------------|-------------|------------------------------------------------------------------|
| `SKU`             | no          | Con SKU el ítem es **normal**; vacío, es **manual**              |
| `Descripción`     | sí          | Ítem manual: es la descripción en GBP. Ítem normal: sólo se muestra en el CRM (GBP trae la suya) |
| `Part Number`     | no          | Se ignora                                                        |
| `Cantidad`        | sí          | Cantidad                                                         |
| `Precio Unitario` | sí          | Precio **neto (sin IVA)** que se cotiza al cliente               |
| `Moneda`          | sí          | `ARS` o `USD` (moneda de esa línea)                              |
| `IVA %`           | sí          | `0.21` o `0.105`. En GBP sólo se usa para los ítems manuales     |

Si el archivo tiene una columna `Costo` y no tiene `Precio Unitario`, se rechaza
con este mensaje: *"El archivo trae 'Costo' en vez de 'Precio Unitario'. Revisá
que los valores sean precios de venta netos y renombrá la columna."* Así se evita
cotizar al costo por usar una planilla vieja.

## 2. Validación por fila

Se juntan **todos** los errores, cada uno con su número de fila del Excel, y se
muestran juntos. Si hay al menos un error, **no se encola nada**.

| Campo           | Regla                                                                                |
|-----------------|---------------------------------------------------------------------------------------|
| SKU             | Sin espacios al inicio ni al final, en mayúsculas. Vacío: ítem manual                 |
| Descripción     | Obligatoria si el ítem es manual                                                      |
| Cantidad        | Número entero mayor que 0                                                             |
| Precio Unitario | Número mayor que 0. Se **redondea a 2 decimales**                                     |
| Moneda          | Sin espacios, en mayúsculas. Sólo `ARS` o `USD`                                       |
| IVA %           | Sólo `0.21` o `0.105`. También se aceptan `21` y `10.5`. Se normaliza a `21` / `10.5` |
| Control de rango | `USD` con precio mayor que **20.000**: error *"¿precio en pesos marcado como USD?"* (umbral configurable) |

Los SKUs repetidos están permitidos: cada fila es una línea separada, tal cual
viene en el archivo.

## 3. Datos del encabezado (se cargan en el CRM, no vienen en el Excel)

| Campo           | Valores                                                       | Default             |
|-----------------|---------------------------------------------------------------|---------------------|
| `cliente_numero`| Número de cliente en GBP (sale de la oportunidad o se carga a mano) | —             |
| `documento`     | `Presupuesto USD` \| `Presupuesto ARS`                        | `Presupuesto USD`   |
| `entrega_por`   | `Retiro TML - Garin` \| `TML Corpo`                           | —                   |
| `sucursal`      | Valor de la sucursal en GBP (`1` = 01.ARG. COLOR)             | `1`                 |
| `fecha_entrega` | Siempre `today`                                               | `today`             |

## 4. Partición en jobs

- Partes de **20 ítems** (configurable: `GBP_MAX_ITEMS`), **respetando el orden
  del Excel**. Ejemplo: 322 ítems generan 17 jobs (16 de 20 y uno de 2).
- Todas las partes de un mismo archivo comparten un identificador de lote y
  llevan `parte` / `total_partes`. El CRM las muestra agrupadas en la
  oportunidad ("parte 3 de 17").
- La partición se hace **una sola vez** al subir el archivo y no se rehace. Si
  una parte falla, se corrige y se reencola **sólo esa parte**: las que ya
  están en estado `cargado` no se tocan.

## 5. Formato de cada job (`GET /api/v1/gbp-bot/jobs/next`)

```json
{
  "id": 123,
  "lote_id": 45,
  "parte": 3,
  "total_partes": 17,
  "cliente_numero": "46",
  "documento": "Presupuesto USD",
  "entrega_por": "Retiro TML - Garin",
  "sucursal": "1",
  "fecha_entrega": "today",
  "items": [
    { "tipo": "normal", "sku": "VENKAN0017", "descripcion": "Ventilador KanjiHome...",
      "cantidad": 2, "precio": 36.0, "moneda": "USD", "iva": null },
    { "tipo": "manual", "sku": null, "descripcion": "Flete",
      "cantidad": 2, "precio": 5000.0, "moneda": "ARS", "iva": 21 }
  ]
}
```

- En los ítems `normal`, `iva` va en `null` (GBP usa el IVA del artículo).
- `lote_id`, `parte`, `total_partes` y `sucursal` son campos **nuevos** respecto
  del contrato actual. El bot los acepta; si falta `sucursal`, usa `1`.

## 6. Qué hace el bot con cada job

1. Carga el job completo en un solo pedido de GBP.
2. Ante cualquier problema (SKU inexistente en GBP, cliente que no se encuentra,
   moneda o IVA inválidos, GBP rechaza un ítem) **cancela sin guardar** y llama
   a `POST /jobs/{id}/error` con un mensaje concreto, por ejemplo:
   *"Ítem 7 (TONHPC0514): el SKU no existe en GBP"*. Nunca guarda una parte a
   medias.
3. Si todo sale bien, guarda y llama a `POST /jobs/{id}/resultado` con
   `numero_gbp` (formato `#1-598168`) y el PDF.

Tiempo estimado: alrededor de 18 segundos por ítem (unos 6 minutos por parte de 20).
