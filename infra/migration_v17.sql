-- Migration v17: Fix LGPD ERASED status constraint
--
-- Problema: o schema tinha um CHECK constraint legado (`players_status_check`) em
-- players.status que não incluía 'ERASED', conflitando com `chk_player_status`.
-- Isso fazia operações de LGPD (POST /players/{id}/erase) falharem ao tentar
-- persistir status='ERASED'.
--
-- Solução: remove o constraint legado e mantém `chk_player_status` como fonte de verdade.

ALTER TABLE players
    DROP CONSTRAINT IF EXISTS players_status_check;
