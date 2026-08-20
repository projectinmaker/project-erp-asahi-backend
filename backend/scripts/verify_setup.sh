#!/bin/bash
# ============================================
# Verify Development Setup
# ============================================

set -e

echo "========================================"
echo "  Asahi ERP - Setup Verification"
echo "========================================"
echo ""

ERRORS=0

# 1. Check Python version
echo -n "Checking Python version (3.11+)... "
PYTHON_VERSION=$(python3.11 --version 2>&1 | awk '{print $2}')
if [[ "$PYTHON_VERSION" =~ ^3\.(11|12|13) ]]; then
    echo "✓ $PYTHON_VERSION"
else
    echo "✗ $PYTHON_VERSION (need 3.11+)"
    ERRORS=$((ERRORS + 1))
fi

# 2. Check Docker
echo -n "Checking Docker... "
if docker info > /dev/null 2>&1; then
    echo "✓ Running"
else
    echo "✗ Not running"
    ERRORS=$((ERRORS + 1))
fi

# 3. Check PostgreSQL container
echo -n "Checking PostgreSQL container... "
if docker ps | grep -q "asahi-erp-postgres"; then
    echo "✓ Running"
else
    echo "✗ Not running (run: docker-compose up -d)"
    ERRORS=$((ERRORS + 1))
fi

# 4. Check virtual environment
echo -n "Checking virtual environment... "
if [ -d "venv" ]; then
    echo "✓ Exists"
else
    echo "✗ Not found (run: python -m venv venv)"
    ERRORS=$((ERRORS + 1))
fi

# 5. Check .env file
echo -n "Checking .env file... "
if [ -f ".env" ]; then
    echo "✓ Exists"
else
    echo "✗ Not found"
    ERRORS=$((ERRORS + 1))
fi

# 6. Check project structure
echo -n "Checking project structure... "
if [ -f "app/main.py" ] && [ -f "app/config.py" ] && [ -f "app/database.py" ]; then
    echo "✓ OK"
else
    echo "✗ Incomplete"
    ERRORS=$((ERRORS + 1))
fi

echo ""
echo "========================================"
if [ $ERRORS -eq 0 ]; then
    echo "  ✓ All checks passed!"
    echo "  Run: ./scripts/start.sh"
else
    echo "  ✗ $ERRORS check(s) failed"
    echo "  Please fix the errors above"
fi
echo "========================================"

exit $ERRORS