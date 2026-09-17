#!/bin/bash
# ============================================
# Create Test Database
# ============================================

echo "Creating test database if not exists..."

docker exec -i asahi-erp-postgres psql -U asahi_dev -d postgres <<EOF
SELECT 'CREATE DATABASE asahi_erp_test OWNER asahi_dev'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'asahi_erp_test')
\gexec
EOF

echo "Test database ready!"