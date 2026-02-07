\c apexgps_db

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS postgis_raster;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;
CREATE EXTENSION IF NOT EXISTS postgis_tiger_geocoder;
CREATE EXTENSION IF NOT EXISTS pgrouting;

SELECT 'Checking pgRouting installation...' as info;

DO $$
BEGIN
    BEGIN
        PERFORM pgr_version();
        RAISE NOTICE 'pgRouting is installed and working!';
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'pgRouting pgr_version() failed: %', SQLERRM;
    END;
END $$;

SELECT 'Total pgRouting functions:' as description,
       COUNT(*) as count
FROM pg_proc
WHERE proname LIKE 'pgr_%';

SELECT 'Topology functions found:' as description,
       proname as function_name
FROM pg_proc
WHERE proname ILIKE '%topology%'
   OR proname ILIKE '%createtopology%'
ORDER BY proname;

SELECT 'pgr_createTopology exists:' as description,
       CASE
           WHEN EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'pgr_createtopology')
           THEN 'YES'
           ELSE 'NO'
       END as exists;