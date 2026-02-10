# Troubleshooting Guide

## Common Issues

### 1. Git Clone Fails (Permission Denied)

**Problem**: `git@github.com: Permission denied (publickey)`

**Solution**:
```bash
# Generate SSH key
ssh-keygen -t ed25519 -C "bot@yourdomain.com"

# Add to GitHub
cat ~/.ssh/id_ed25519.pub
# Copy to GitHub → Settings → SSH and GPG keys → New SSH key
```

### 2. Docker Not Working

**Problem**: `docker: command not found`

**Solution**:
```bash
sudo apt-get update
sudo apt-get install docker.io docker-compose
sudo usermod -aG docker bot
# Re-login or reboot
```

### 3. Port Already in Use

**Problem**: `Port 3000 is already in use`

**Solution**:
```bash
# Find process using port
lsof -i :3000

# Kill it
kill -9 <PID>

# Or change port in vite.config.ts
```

### 4. Database Connection Failed

**Problem**: `Connection refused on port 5433`

**Solution**:
```bash
# Start PostgreSQL
sudo systemctl start postgresql

# Or use Docker Compose
cd ~/projects/property_management
docker-compose up -d postgres
```

### 5. Node/NPM Version Too Old

**Problem**: `npm ERR! node@v18.x.x`

**Solution**:
```bash
# Install Node 20
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
```

## Quick Fixes

### Reset OpenClaw Configuration
```bash
rm -rf ~/.openclaw
cp -r ~/bot-deploy-setup/openclaw-config/* ~/.openclaw/
```

### Re-clone All Projects
```bash
rm -rf ~/projects/*
cd ~/bot-deploy-setup
./setup.sh
```

### Check Services Status
```bash
cd ~/projects/property_management
./start-all.sh status
```

## Getting Help

1. Check logs: `docker-compose logs -f`
2. Check health: `curl http://localhost:8015/health`
3. Review this file for common issues
