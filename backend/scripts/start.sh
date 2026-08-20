#!/bin/bash
# ============================================
# Asahi ERP - Development Start Script
# ============================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}   Asahi ERP - Development Server${NC}"
echo -e "${BLUE}============================================${NC}"

# Check if docker is running
echo -e "${YELLOW}[1/4] Checking Docker...${NC}"
if ! docker info > /dev/null 2>&1; then
    echo -e "${RED}Error: Docker is not running!${NC}"
    echo "Please start Docker first."
    exit 1
fi
echo -e "${GREEN}✓ Docker is running${NC}"

# Check if PostgreSQL container is running
echo -e "${YELLOW}[2/4] Checking PostgreSQL container...${NC}"
if ! docker-compose ps postgres | grep -q "running\|Up"; then
    echo -e "${YELLOW}Starting PostgreSQL container...${NC}"
    docker-compose up -d postgres
    sleep 5
fi
echo -e "${GREEN}✓ PostgreSQL container is running${NC}"

# Check if virtual environment exists
echo -e "${YELLOW}[3/4] Checking virtual environment...${NC}"
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python -m venv venv
fi
echo -e "${GREEN}✓ Virtual environment ready${NC}"

# Activate venv and check dependencies
echo -e "${YELLOW}[4/4] Starting development server...${NC}"
source venv/bin/activate

echo -e "${BLUE}============================================${NC}"
echo -e "${GREEN}Starting FastAPI server...${NC}"
echo -e "${BLUE}============================================${NC}"
echo -e "API:      ${GREEN}http://localhost:8000${NC}"
echo -e "Docs:     ${GREEN}http://localhost:8000/docs${NC}"
echo -e "ReDoc:    ${GREEN}http://localhost:8000/redoc${NC}"
echo -e "pgAdmin:  ${GREEN}http://localhost:5050${NC}"
echo -e "${BLUE}============================================${NC}"

# Start uvicorn with hot reload
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload