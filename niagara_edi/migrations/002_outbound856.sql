ALTER TABLE central_artifacts ADD COLUMN parse_status TEXT NOT NULL DEFAULT 'NOT_PARSED';
ALTER TABLE central_artifacts ADD COLUMN parse_error TEXT;
ALTER TABLE central_artifacts ADD COLUMN parser_version TEXT;
ALTER TABLE central_documents ADD COLUMN radius_bol_number INTEGER;
ALTER TABLE central_documents ADD COLUMN radius_customer_code TEXT;
ALTER TABLE central_documents ADD COLUMN company_number INTEGER;
ALTER TABLE central_documents ADD COLUMN plant_code TEXT;
CREATE TABLE central_document_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES central_documents(id),
    radius_shipreq_line INTEGER NOT NULL,
    radius_so_number INTEGER NOT NULL,
    radius_so_line INTEGER NOT NULL,
    customer_po TEXT NOT NULL DEFAULT '',
    UNIQUE(document_id,radius_shipreq_line)
);
CREATE INDEX idx_central_documents_bol ON central_documents(radius_bol_number);
CREATE INDEX idx_central_documents_sr ON central_documents(radius_shipreq_number);
CREATE INDEX idx_central_lines_so ON central_document_lines(radius_so_number);
CREATE INDEX idx_central_lines_po ON central_document_lines(customer_po);
