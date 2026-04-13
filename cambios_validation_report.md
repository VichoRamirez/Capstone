1. Solo queremos mostrar que el algoritmo es eficiente y bueno al momento de encontrar soluciones.
2. Hay que apretar un poco la dispersion de los datos.
3. Las restricciones en orden de prioridad son:
    - Número de camiones (Dura)
    - Tiempo máximo: 5 horas (Dura)
    - Capacidad de los camiones (Dura)
    - Que se entreguen todos los pedidos. (Blanda) (obviamente, que no se envíen tiene que tener un gran castigo)

4. Para medir los resultados, necesito los siguientes KPI:
    - Tiempo de operación.
    - Distancia total recorrida.
    - Número de camiones utilizados.
    - Porcentaje de envíos cubiertos
    - Largo máximo de ruta
    - Largo promedio de ruta
    - índice de tortuosidad ($$ T= \frac{D_{total}}{D_{linea}} $$)

5. El tabú search debe ser limitado a 20 segundos.

6. Si la solución del tabú es peor que la del solomon, nos quedamos con la del solomon (aún así se considera el tiempo de tabú)

7. El TC-4 vamos a hacerlo con 800 nodos en toda la RM, con una demanda de 50-150 kg y 40 camiones de 3000 kg.
