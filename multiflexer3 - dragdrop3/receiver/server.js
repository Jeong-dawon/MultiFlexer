// server.js
const express = require('express');
const bodyParser = require('body-parser');
const { spawn } = require('child_process');
const path = require('path');

const app = express();
const PORT = 4000;

app.use(bodyParser.json());
app.use(express.static(path.join(__dirname, 'public'))); // index.html, main.js 등

app.post('/start-stream', (req, res) => {
  const { senderId } = req.body;
  if (!senderId) return res.status(400).json({ error: 'Missing senderId' });

  const room = 'your-room-password'; // 필요시 동적으로 설정
  const args = [
    'receiver_gst.py',
    '--sender=' + senderId,
    '--room=' + room,
    '--signal=http://localhost:3001'
  ];

  const pythonProcess = spawn('python3', args, { cwd: __dirname });

  pythonProcess.stdout.on('data', (data) => {
    console.log(`[GST STDOUT] ${data}`);
  });

  pythonProcess.stderr.on('data', (data) => {
    console.error(`[GST STDERR] ${data}`);
  });

  pythonProcess.on('close', (code) => {
    console.log(`[GST] 프로세스 종료: code ${code}`);
  });

  res.status(200).json({ message: 'receiver_gst started' });
});

app.listen(PORT, () => {
  console.log(`Receiver HTTP server running at http://localhost:${PORT}`);
});
