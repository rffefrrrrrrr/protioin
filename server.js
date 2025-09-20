const express = require('express');
const cors = require('cors');
const { spawn } = require('child_process');
const path = require('path');

const app = express();
const port = process.env.PORT || 4000;

// Middleware
app.use(cors());
app.use(express.json());

// Bot process variable
let botProcess = null;
let botStatus = 'stopped';
let lastHeartbeat = new Date();

// Function to start the Python bot
function startBot() {
    if (botProcess) {
        console.log('Bot is already running');
        return;
    }

    console.log('Starting Telegram protection bot...');
    
    // Start the Python bot process
    botProcess = spawn('python3', ['-c', 'from protection_bot import start_bot; start_bot()'], {
        cwd: __dirname,
        stdio: ['pipe', 'pipe', 'pipe']
    });

    botStatus = 'running';
    lastHeartbeat = new Date();

    // Handle bot output
    botProcess.stdout.on('data', (data) => {
        console.log(`Bot stdout: ${data}`);
        lastHeartbeat = new Date();
    });

    botProcess.stderr.on('data', (data) => {
        console.error(`Bot stderr: ${data}`);
        lastHeartbeat = new Date();
    });

    // Handle bot process exit
    botProcess.on('close', (code) => {
        console.log(`Bot process exited with code ${code}`);
        botStatus = 'stopped';
        botProcess = null;
        
        // Restart bot after 5 seconds if it crashes
        setTimeout(() => {
            console.log('Restarting bot after crash...');
            startBot();
        }, 5000);
    });

    botProcess.on('error', (error) => {
        console.error(`Bot process error: ${error}`);
        botStatus = 'error';
        botProcess = null;
    });
}

// Function to stop the bot
function stopBot() {
    if (botProcess) {
        console.log('Stopping bot...');
        botProcess.kill("SIGTERM"); // Send SIGTERM for graceful shutdown
        // Force kill after a timeout if it doesn\'t exit gracefully
        const killTimeout = setTimeout(() => {
            if (botProcess && !botProcess.killed) {
                console.log("Bot did not terminate gracefully, sending SIGKILL...");
                botProcess.kill("SIGKILL");
            }
        }, 5000); // Wait 5 seconds for graceful shutdown
        botProcess.on('close', () => clearTimeout(killTimeout));
        botProcess = null;
        botStatus = 'stopped';
    }
}

// Routes
app.get('/', (req, res) => {
    res.json({
        message: 'Telegram Protection Bot Server',
        status: botStatus,
        lastHeartbeat: lastHeartbeat,
        uptime: process.uptime(),
        timestamp: new Date().toISOString()
    });
});

// Health check endpoint for Render
app.get('/health', (req, res) => {
    const healthStatus = {
        status: 'healthy',
        botStatus: botStatus,
        lastHeartbeat: lastHeartbeat,
        uptime: process.uptime(),
        timestamp: new Date().toISOString()
    };
    
    res.status(200).json(healthStatus);
});

// Keep alive endpoint
app.get('/ping', (req, res) => {
    res.json({
        message: 'pong',
        timestamp: new Date().toISOString(),
        botStatus: botStatus
    });
});

// Bot control endpoints
app.post('/bot/start', (req, res) => {
    startBot();
    res.json({
        message: 'Bot start command sent',
        status: botStatus
    });
});

app.post('/bot/stop', (req, res) => {
    stopBot();
    res.json({
        message: 'Bot stop command sent',
        status: botStatus
    });
});

app.post('/bot/restart', (req, res) => {
    stopBot();
    setTimeout(() => {
        startBot();
    }, 2000);
    res.json({
        message: 'Bot restart command sent'
    });
});

// Bot status endpoint
app.get('/bot/status', (req, res) => {
    res.json({
        status: botStatus,
        lastHeartbeat: lastHeartbeat,
        processId: botProcess ? botProcess.pid : null,
        uptime: process.uptime()
    });
});

// Error handling middleware
app.use((err, req, res, next) => {
    console.error(err.stack);
    res.status(500).json({
        error: 'Something went wrong!',
        message: err.message
    });
});

// Handle 404
app.use((req, res) => {
    res.status(404).json({
        error: 'Not Found',
        message: 'The requested endpoint does not exist'
    });
});

// Graceful shutdown
process.on('SIGTERM', () => {
    console.log('Received SIGTERM, shutting down gracefully...');
    stopBot();
    process.exit(0);
});

process.on('SIGINT', () => {
    console.log('Received SIGINT, shutting down gracefully...');
    stopBot();
    process.exit(0);
});

// Start the server
app.listen(port, '0.0.0.0', () => {
    console.log(`🚀 Server running on port ${port}`);
    console.log(`📊 Health check: http://localhost:${port}/health`);
    console.log(`🤖 Bot status: http://localhost:${port}/bot/status`);
    
    // Start the bot automatically when server starts
    setTimeout(() => {
        startBot();
    }, 2000);
});

// Keep the process alive with periodic heartbeat
setInterval(() => {
    console.log(`💓 Server heartbeat - Bot status: ${botStatus} - ${new Date().toISOString()}`);
    
    // Check if bot is still responsive (no output for more than 10 minutes)
    const timeSinceLastHeartbeat = Date.now() - lastHeartbeat.getTime();
    if (timeSinceLastHeartbeat > 10 * 60 * 1000 && botStatus === 'running') {
        console.log('⚠️ Bot seems unresponsive, restarting...');
        stopBot();
        setTimeout(() => {
            startBot();
        }, 3000);
    }
}, 60000); // Every minute

