-- Кредиты: предоплата, из которой вычитается расход.
--
-- 1 кредит = 1 ₽, храним в копейках — той же единицей, что и расход, иначе
-- баланс складывался бы из двух валют. Журнал начислений, а не столбец
-- «баланс» в accounts: по столбцу не ответить, кто и когда начислил, а именно
-- это спрашивают, когда сумма не сходится.
CREATE TABLE IF NOT EXISTS credits (
    id          BIGSERIAL   PRIMARY KEY,
    account_id  BIGINT      NOT NULL REFERENCES accounts (id) ON DELETE RESTRICT,
    -- Может быть отрицательной: корректировка — тоже начисление, только со
    -- знаком минус. Отдельного «списания» не нужно: расход считается из
    -- журнала аренд и токенов, а не пишется сюда.
    kopecks     BIGINT      NOT NULL,
    note        TEXT        NOT NULL DEFAULT '',
    -- Кто начислил. Пусто — аварийный вход по токену.
    granted_by  BIGINT      REFERENCES accounts (id) ON DELETE SET NULL,
    at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS credits_account_idx ON credits (account_id, at);

-- Стоимость ответа — в момент ответа, по прайсу того дня. Ставка записывается
-- в запись по той же причине, что и в аренду: поднятая завтра цена не должна
-- переписывать вчерашние счета.
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS cost BIGINT NOT NULL DEFAULT 0;
