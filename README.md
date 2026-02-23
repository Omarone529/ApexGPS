# ApexGPS - Servizio API Back-end

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Made with-Django](https://img.shields.io/badge/Backend-Django-092E20?logo=django&logoColor=white)
![API-DRF](https://img.shields.io/badge/API-DRF-FF5432?logo=djangorestframework&logoColor=white)
![GIS-PostGIS/pgRouting](https://img.shields.io/badge/GIS-PostGIS%2FpgRouting-4169E1?logo=postgresql&logoColor=white)
![Architecture-REST API](https://img.shields.io/badge/Architecture-REST%20API-7B8997)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)
![Containerized-Docker](https://img.shields.io/badge/Containerized-Docker-2496ED?logo=docker&logoColor=white)

![Logo](./assets/logo/ApexGPS_logo.png)

## 1. Panoramica del Progetto

Questo documento descrive il servizio backend API del progetto, l'infrastruttura centrale responsabile del calcolo e della gestione dei percorsi di guida ottimizzati.

Lo scopo è fornire un motore di routing avanzato che genera percorsi panoramici e sinuosi, dando priorità all'esperienza di guida, pur mantenendo un vincolo di tempo ragionevole rispetto al percorso più veloce.

Funzionalità Chiave:
- Calcolo Percorso Panoramico: Genera percorsi unici che massimizzano la sinuosità e l'interesse panoramico.
- Tolleranza Tempo: Il percorso panoramico non supera il percorso più veloce di indicativamente 40 minuti.
- API Universale: Serve un'unica interfaccia dati (API) per le applicazioni mobile e web.
- Persistenza Utente: Gestisce l'autenticazione (JWT e OAuth) e il salvataggio/sincronizzazione dei percorsi per ciascun utente.
## 2. Architettura e Stack Tecnologico

Il backend è un'applicazione indipendente basata su un'architettura GIS (Geographic Information System) open-source, fornendo il servizio esclusivamente tramite API REST.

### Logica di Routing (Core GIS)

Il calcolo del percorso è gestito da una funzione di costo custom all'interno del grafo di pgRouting.
La funzione di costo C per ogni segmento stradale è definita come:

C_segmento = (α⋅Distanza)−(β⋅Stotale)

S totale (Punteggio Panoramico) è un valore pre-calcolato che combina:
- Sinuosità (curve)
- Variazione di Altitudine
- Prossimità ai POI.

α e β sono coefficienti che vengono regolati in base alla preferenza dell'utente.

## 3. Guida al Setup Locale

Questa sezione è destinata agli sviluppatori per la configurazione dell'ambiente.

Prerequisiti

Il progetto è completamente containerizzato con Docker e Docker Compose, includendo sia l'applicazione Django che il database PostgreSQL con PostGIS/pgRouting.
Assicurarsi che siano installati i seguenti servizi a livello di sistema:

    PostgreSQL (versione 14+)

    PostGIS e pgRouting (abilitati come estensioni nel database)

    Python 3.10+

    Docker Engine 24.0+

    Docker Compose v2.20+

Installazione e Avvio

Clonazione e Dipendenze

    Clonare la repository, creare e attivare l'ambiente virtuale.

    Installare le dipendenze richieste dal file requirements.txt.

### Bash

# Clonare la repository
git clone https://github.com/Omarone529/ApexGPS
cd ApexGPS

# Avviare tutti i servizi (Django + PostgreSQL + PostGIS)

docker compose build

docker compose up

# Eseguire le migrazioni del database
docker-compose exec web python manage.py migrate

# Creare un superuser (facoltativo)
docker compose exec web python manage.py createsuperuser

# L'applicazione sarà disponibile all'indirizzo: http://localhost:8000

### Configurazione Database e Migrazioni

Configurare le credenziali del DB PostGIS e assicurarsi che l'engine sia django.contrib.gis.db.backends.postgis.

python manage.py migrate
Popolamento Dati Spaziali
Eseguire lo script di gestione Django che costruisce il grafo navigabile e pre-calcola i punteggi panoramici (Stotale​).

Esegue la creazione della topologia (pgr_createTopology) e il pre-calcolo dei punteggi panoramici.
python manage.py prepare_gis_data --area {codice_area}
python manage.py runserver