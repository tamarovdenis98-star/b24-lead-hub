-- 011: cap on leads in processing at once. Ticks of 02 process overlap when a batch takes longer than
-- 15 s; with a large backlog the number of parallel workers grew without bound and n8n (1 GB limit on a
-- 1 vCPU / 2 GB VPS) reached 947 MB during the second load replay. A tick now claims only the free room.
INSERT INTO settings (key, value) VALUES ('process_max_in_flight', '20') ON CONFLICT (key) DO NOTHING;
