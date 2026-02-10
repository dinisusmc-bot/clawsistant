#!/bin/bash
set -e

echo "=== Bot Deployment Setup ==="
echo "Starting deployment on $(date)"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as bot user
if [ "$USER" != "bot" ]; then
    echo -e "${RED}ERROR: This script must run as 'bot' user${NC}"
    exit 1
fi

# Step 1: Install dependencies
echo -e "${YELLOW}Step 1/6: Installing dependencies...${NC}"
sudo apt-get update -qq
sudo apt-get install -y -qq nodejs npm python3 python3-pip docker.io docker-compose git curl > /dev/null 2>&1 || true

# Verify installations
node --version
npm --version
python3 --version
docker --version
docker-compose --version

echo -e "${GREEN}✓ Dependencies installed${NC}"
echo ""

# Step 2: Configure OpenClaw
echo -e "${YELLOW}Step 2/6: Configuring OpenClaw...${NC}"

# Copy OpenClaw config from this setup
if [ -d "$HOME/bot-deploy-setup/openclaw-config" ]; then
    cp -r "$HOME/bot-deploy-setup/openclaw-config"/* "$HOME/.openclaw/" 2>/dev/null || true
    echo "✓ OpenClaw config copied"
else
    echo "⚠ No OpenClaw config found, using existing"
fi

echo -e "${GREEN}✓ OpenClaw configured${NC}"
echo ""

# Step 3: Clone projects
echo -e "${YELLOW}Step 3/6: Cloning project repositories...${NC}"

mkdir -p ~/projects

# Clone property_management
if [ ! -d ~/projects/property_management ]; then
    echo "Cloning property_management..."
    git clone git@github.com:dinisusmc-bot/property_management.git ~/projects/property_management
fi

# Clone atlas
if [ ! -d ~/projects/atlas ]; then
    echo "Cloning atlas..."
    git clone git@github.com:dinisusmc-bot/atlas.git ~/projects/atlas
fi

# Clone animal-rescue
if [ ! -d ~/projects/animal-rescue ]; then
    echo "Cloning animal-rescue..."
    git clone git@github.com:dinisusmc-bot/animal-rescue.git ~/projects/animal-rescue
fi

echo -e "${GREEN}✓ Projects cloned${NC}"
echo ""

# Step 4: Set up environment
echo -e "${YELLOW}Step 4/6: Setting up environment variables...${NC}"

if [ -f "$HOME/bot-deploy-setup/secrets/.env" ]; then
    cp "$HOME/bot-deploy-setup/secrets/.env" "$HOME/.env"
    echo "✓ Environment variables copied"
else
    echo -e "${YELLOW}⚠ No .env file found. Create one manually${NC}"
fi

echo -e "${GREEN}✓ Environment configured${NC}"
echo ""

# Step 5: Install project dependencies
echo -e "${YELLOW}Step 5/6: Installing project dependencies...${NC}"

# Install Node dependencies
cd ~/projects/property_management/frontend && npm ci --quiet 2>/dev/null || npm install --quiet
cd ~/projects/alebrook/alebrook-app && npm ci --quiet 2>/dev/null || npm install --quiet

echo -e "${GREEN}✓ Dependencies installed${NC}"
echo ""

# Step 6: Start services
echo -e "${YELLOW}Step 6/6: Starting services...${NC}"

cd ~/projects/property_management
./start-all.sh 2>/dev/null || docker-compose up -d 2>/dev/null || true

echo -e "${GREEN}✓ Services started${NC}"
echo ""

# Summary
echo -e "${GREEN}=== Deployment Complete ===${NC}"
echo ""
echo "Access points:"
echo "  - Property Management: http://localhost:3002"
echo "  - Atlas (admin): http://localhost:3000"
echo "  - Alebrook: http://localhost:3001"
echo ""
echo "Project locations:"
echo "  - ~/projects/property_management"
echo "  - ~/projects/atlas"
echo "  - ~/projects/animal-rescue"
echo ""
echo "Next steps:"
echo "  1. Configure your environment variables in ~/.env"
echo "  2. Test each application"
echo "  3. Review logs: docker-compose logs -f"
echo ""
