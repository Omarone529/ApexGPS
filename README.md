# ApexGPS - Servizio API Back-end

[![pipeline status](https://gitlab.com/{user}/{repo}/badges/main/pipeline.svg)](https://gitlab.com/{user}/{repo}/-/commits/main)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Made with-Django](https://img.shields.io/badge/Backend-Django-092E20?logo=django&logoColor=white)
![API-DRF](https://img.shields.io/badge/API-DRF-FF5432?logo=djangorestframework&logoColor=white)
![GIS-PostGIS/pgRouting](https://img.shields.io/badge/GIS-PostGIS%2FpgRouting-4169E1?logo=postgresql&logoColor=white)
![Architecture-REST API](https://img.shields.io/badge/Architecture-REST%20API-7B8997)


## 1. Panoramica del Progetto

Questo documento descrive il servizio backend API del progetto, l'infrastruttura centrale responsabile del calcolo e della gestione dei percorsi di guida ottimizzati.

Lo scopo è fornire un motore di routing avanzato che genera percorsi panoramici e sinuosi, dando priorità all'esperienza di guida, pur mantenendo un vincolo di tempo ragionevole rispetto al percorso più veloce.

Funzionalità Chiave:
- Calcolo Percorso Smart: Genera percorsi unici che massimizzano la sinuosità e l'interesse panoramico.
- Tolleranza Tempo: Il percorso panoramico non supera il percorso più veloce di indicativamente 40 minuti (vincolo operativo critico).
- API Universale: Serve un'unica interfaccia dati (API) per le applicazioni mobile e web.
- Persistenza Utente: Gestisce l'autenticazione (JWT) e il salvataggio/sincronizzazione dei percorsi per ciascun utente.
## 2. Architettura e Stack Tecnologico

Il backend è un'applicazione disaccoppiata basata su un'architettura GIS (Geographic Information System) open-source, fornendo un servizio esclusivamente tramite API REST.

### Logica di Routing (Core GIS)

Il calcolo del percorso è gestito da una funzione di costo custom all'interno del grafo di pgRouting.
La funzione di costo C per ogni segmento stradale è definita come:
Csegmento​=(α⋅Distanza)−(β⋅Stotale​)

S totale​ (Punteggio Panoramico) è un valore pre-calcolato che combina: 
- Sinuosità (curve)
- Variazione di Altitudine
- Prossimità ai POI.

α e β sono coefficienti che vengono regolati in base alla preferenza dell'utente.

## 3. Guida al Setup Locale

Questa sezione è destinata agli sviluppatori per la configurazione dell'ambiente.

Prerequisiti

Assicurarsi che siano installati i seguenti servizi a livello di sistema:

    PostgreSQL (versione 14+)

    PostGIS e pgRouting (abilitati come estensioni nel database)

    Python 3.10+

Installazione e Avvio

Clonazione e Dipendenze

    Clonare la repository, creare e attivare l'ambiente virtuale.

    Installare le dipendenze richieste dal file requirements.txt.

### Bash

git clone https://github.com/Omarone529/ApexGPS
cd ApexGPS
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

### Configurazione Database e Migrazioni

Configurare le credenziali del DB PostGIS e assicurarsi che l'engine sia django.contrib.gis.db.backends.postgis.

python manage.py migrate
Popolamento Dati Spaziali
Eseguire lo script di gestione Django che costruisce il grafo navigabile e pre-calcola i punteggi panoramici (Stotale​).

Esegue la creazione della topologia (pgr_createTopology) e il pre-calcolo dei punteggi panoramici.
python manage.py prepare_gis_data --area {codice_area}
python manage.py runserver

## 4. Documentazione API (Per Sviluppatori Frontend)

L'interfaccia di comunicazione è standard REST, con risposte in formato JSON.

Calcolo del Percorso (POST /api/routes/calculate/)

| Parametro | Tipo  | Descrizione |
| :--- |:------| :--- |
| **`start_lat`**, **`start_lon`** | float | Coordinate geografiche (latitudine e longitudine) del **punto di partenza**. |
| **`end_lat`**, **`end_lon`** | float | Coordinate geografiche (latitudine e longitudine) del **punto di arrivo**. |
| **`preference`** | string| Livello di ottimizzazione del percorso panoramico. Valori: `"veloce"`, `"equilibrata"`, `"sinuosa_massima"`. |


Risposta di Successo (Estratto):
```
JSON
{
  "distance_km": 300.5,
  "estimated_time_min": 240,
  "polyline": "encoded_polyline_string_per_mappa"
}
```
Gestione Percorsi Utente (CRUD)

Questi endpoint richiedono l'invio del token JWT nell'intestazione Authorization.

| Endpoint | Metodo | Funzione |
| :--- | :--- | :--- |
| `/api/saved_routes/` | `GET` / `POST` | Recupera l'elenco dei percorsi salvati o ne salva uno nuovo. |
| `/api/saved_routes/{id}/` | `DELETE` | Elimina un percorso specifico tramite il suo ID. |