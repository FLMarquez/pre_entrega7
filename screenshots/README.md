# Capturas requeridas

Colocá acá las capturas pedidas por la consigna, generadas después de correr
`python scripts/load_test.py` con la API y Redis levantados:

1. `traces.png` - vista del dashboard (LangSmith o Phoenix) mostrando las trazas
   de las 5 ejecuciones, con el detalle de nodos del grafo (planner, classifier,
   human_approval, executor, reporter).
2. `cost.png` - costo por ejecución calculado por la plataforma a partir de los
   tokens de entrada/salida, para la corrida de 5 peticiones concurrentes.
3. `latency_p95.png` - latencia p95 de esa misma corrida.
4. `hitl.png` (opcional pero recomendado) - una traza que muestre la pausa
   human-in-the-loop y su resolución (aprobación/rechazo).

Ver el README principal, sección "Prueba de carga y lectura del dashboard".
