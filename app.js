// --- KONFIGURATION ---
// Ändra till din publika backend-URL när du deployar till Cloud Run / Render
const API_BASE_URL = "http://127.0.0.1:8000";

// --- DOM ELEMENT ---
const views = document.querySelectorAll('.view');
const workoutView = document.getElementById('workout-view');
const errorMessage = document.getElementById('error-message');

// --- STATE ---
let currentWorkoutData = null;
let currentGroupIndex = 0;
let dashboardProgressPieChartInstance = null;
let progressPieChartInstance = null;
let chartInstance = null;
let exerciseChartInstance = null;
let intensityDifficultyChartInstance = null;

const donutCenterTextPlugin = {
    id: 'donutCenterText',
    afterDraw(chart, args, pluginOptions) {
        if (chart.config.type !== 'doughnut') return;

        const { ctx, chartArea } = chart;
        if (!chartArea) return;

        const total = Number(pluginOptions?.total ?? 0);
        const goal = Number(pluginOptions?.goal ?? 1);
        const percent = goal > 0 ? (total / goal) * 100 : 0;
        const centerX = (chartArea.left + chartArea.right) / 2;
        const centerY = (chartArea.top + chartArea.bottom) / 2;

        ctx.save();
        ctx.textAlign = 'center';
        ctx.fillStyle = '#f8fafc';
        ctx.font = '700 28px Inter, sans-serif';
        ctx.fillText(`${percent.toFixed(1)}%`, centerX, centerY - 8);

        ctx.fillStyle = '#cbd5e1';
        ctx.font = '600 12px Inter, sans-serif';
        ctx.fillText(`${Math.round(total).toLocaleString()} / ${Math.round(goal).toLocaleString()} kg`, centerX, centerY + 18);
        ctx.restore();
    }
};

Chart.register(donutCenterTextPlugin);

// --- INITIALISERING & EVENT LISTENERS ---
document.addEventListener('DOMContentLoaded', async () => {
    await loadNextWorkout();
    await loadDashboard();
});

document.getElementById('show-workout-btn').addEventListener('click', () => {
    switchView('workout-view');
});

document.getElementById('show-stats-btn').addEventListener('click', () => {
    switchView('dashboard-view');
    loadDashboard();
});

document.getElementById('refresh-data-btn').addEventListener('click', () => loadNextWorkout(currentGroupIndex));
document.getElementById('home-from-completion-btn').addEventListener('click', () => location.reload());

document.getElementById('difficulty-slider').addEventListener('input', (e) => {
    document.getElementById('difficulty-value').textContent = e.target.value;
});

document.getElementById('submit-difficulty-btn').addEventListener('click', () => {
    const difficulty = parseFloat(document.getElementById('difficulty-slider').value);
    submitWorkoutCompletion(difficulty);
});

document.getElementById('retrain-ml-btn').addEventListener('click', handleRetrainModel);

workoutView.addEventListener('click', async (e) => {
    const backBtn = e.target.closest('.back-btn');
    const completeBtn = e.target.closest('#complete-btn');
    const skipBtn = e.target.closest('#skip-btn');

    if (backBtn) {
        switchView('home-view');
    } else if (completeBtn) {
        await renderCompletionProgress();
        switchView('completion-view');
    } else if (skipBtn) {
        skipWorkout();
    }
});

document.querySelectorAll('.back-btn').forEach(btn => {
    btn.addEventListener('click', () => switchView('home-view'));
});

// --- NAVIGATION ---
function switchView(viewId) {
    views.forEach(v => v.classList.remove('active'));
    document.getElementById(viewId).classList.add('active');
}

// --- API-ANROP ---

/** Hämtar nästa träningspass från FastAPI */
async function loadNextWorkout(groupIndex = null, options = {}) {
    const { returnToHome = true } = options;

    try {
        switchView('loader-view');
        const url = groupIndex !== null 
            ? `${API_BASE_URL}/api/workout/next?group_index=${groupIndex}` 
            : `${API_BASE_URL}/api/workout/next`;
            
        const res = await fetch(url);
        if (!res.ok) throw new Error("Kunde inte hämta pass från servern.");
        
        currentWorkoutData = await res.json();
        currentGroupIndex = currentWorkoutData.groupIndex;
        
        renderWorkoutScreen(currentWorkoutData);
        if (returnToHome) {
            switchView('home-view');
        } else {
            switchView('workout-view');
        }
    } catch (err) {
        showError(err.message);
    }
}

/** Byter till nästa muskelgrupp (Chest -> Back -> Legs) */
async function skipWorkout() {
    currentGroupIndex = (currentGroupIndex + 1) % 3;
    await loadNextWorkout(currentGroupIndex, { returnToHome: false });
}

/** Slutför träningspass och sparar i Google Sheets via FastAPI */
async function submitWorkoutCompletion(difficulty) {
    const btn = document.getElementById('submit-difficulty-btn');
    btn.disabled = true;
    btn.textContent = 'Sparar...';

    try {
        const res = await fetch(`${API_BASE_URL}/api/workout/complete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                workoutType: currentWorkoutData.workoutType,
                originalRowIndex: currentWorkoutData.originalRowIndex,
                difficulty: difficulty
            })
        });

        if (!res.ok) throw new Error("Misslyckades att spara i Google Sheets.");

        await renderCompletionProgress();

        showTempNotification("Träningspasset är sparat!", "success");
        document.getElementById('difficulty-rating-section').style.display = 'none';
        document.getElementById('home-from-completion-btn').style.display = 'block';
    } catch (err) {
        showTempNotification(err.message, "error");
        btn.disabled = false;
        btn.textContent = "Submit Rating";
    }
}

/** Triggar omträning av ML-modellen */
async function handleRetrainModel() {
    const btn = document.getElementById('retrain-ml-btn');
    const originalContent = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<h2 class="text-2xl font-bold text-white">Retraining...</h2><p class="text-gray-400">Vänligen vänta...</p>`;

    try {
        const res = await fetch(`${API_BASE_URL}/api/model/retrain`, { method: 'POST' });
        const data = await res.json();
        showTempNotification(`Modell omtränad!\n${data.serverMessage || ''}`, 'success');
        loadNextWorkout(currentGroupIndex);
    } catch (err) {
        showTempNotification(`Fel vid omträning: ${err.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalContent;
    }
}

/** Hämtar all dashboard-data och ritar upp graferna */
async function loadDashboard() {
    try {
        const res = await fetch(`${API_BASE_URL}/api/dashboard`);
        const data = await res.json();

        if (data.empty) return;

        // 1. Veckomål Progress Pie (Dashboard)
        renderDashboardPie(data.weeklyProgress);

        // 2. 6-Veckors Volymgraf (Line)
        renderVolumeChart(data.volumeChart.labels, data.volumeChart.datasets);

        // 3. Totalt antal genomförda pass (Bar)
        renderSessionsChart(data.sessionsChart.labels, data.sessionsChart.data);

        // 4. Adaptionsgraf
        renderAdaptionChart(data.adaptionChart.datasets, data.adaptionChart.minDate, data.adaptionChart.maxDate);
    } catch (err) {
        console.error("Dashboard error:", err);
    }
}

async function renderCompletionProgress() {
    try {
        const dashboardRes = await fetch(`${API_BASE_URL}/api/dashboard`);
        const dashboardData = await dashboardRes.json();
        if (!dashboardData.empty) {
            renderProgressPie(dashboardData.weeklyProgress);
        }
    } catch (err) {
        console.error('Completion chart error:', err);
    }
}

function renderProgressPie(progress) {
    const ctx = document.getElementById('progress-pie-chart')?.getContext('2d');
    if (!ctx) return;
    if (progressPieChartInstance) progressPieChartInstance.destroy();

    const totalVolume = Number(progress.currentWeekVolume ?? Object.values(progress.volumeByType ?? {}).reduce((sum, value) => sum + Number(value || 0), 0));
    const goalVolume = Number(progress.weeklyGoal ?? 12000);
    const colors = { Chest: '#48BB78', Back: '#F56565', Legs: '#4299E1' };

    const labels = Object.keys(progress.volumeByType || {})
        .filter(k => (progress.volumeByType[k] ?? 0) > 0)
        .map(k => k);
    const dataValues = Object.keys(progress.volumeByType || {})
        .filter(k => (progress.volumeByType[k] ?? 0) > 0)
        .map(k => Number(progress.volumeByType[k] || 0));
    const bgColors = labels.map(label => colors[label] || '#4A5568');

    if (progress.remaining > 0) {
        labels.push('Remaining');
        dataValues.push(Number(progress.remaining || 0));
        bgColors.push('#4A5568');
    }

    progressPieChartInstance = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{ data: dataValues, backgroundColor: bgColors, borderWidth: 0 }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '72%',
            plugins: {
                legend: { display: true, position: 'bottom', labels: { color: 'white', usePointStyle: true } },
                tooltip: {
                    callbacks: {
                        label: (context) => `${context.label}: ${Number(context.parsed).toLocaleString()} kg`
                    }
                },
                donutCenterText: { total: totalVolume, goal: goalVolume }
            }
        }
    });
}

// --- UI RENDERING ---

function renderWorkoutScreen(data) {
    workoutView.innerHTML = `
        <button class="back-btn mb-4 font-semibold text-blue-400 hover:text-blue-300">&larr; Back to Home</button>
        ${data.allCompleted ? `
            <div class="bg-gray-800 p-6 rounded-2xl text-center">
                <p class="text-xl font-bold text-green-400">Alla pass i denna kategori är klara!</p>
            </div>
        ` : `
            <div class="bg-gray-800 p-6 rounded-2xl shadow-xl border border-gray-700">
                <h2 class="text-2xl font-bold text-center text-blue-300">Next Up: ${data.workoutType}</h2>
                <p class="text-center text-gray-400 mb-6">${data.message}</p>
                
                <div class="space-y-3">
                    ${data.exercises.map(ex => `
                        <div class="bg-gray-700 p-4 rounded-lg">
                            <p class="text-lg font-semibold capitalize">${ex.name}</p>
                            <div class="grid grid-cols-3 gap-4 text-center mt-2">
                                <div><p class="text-xs text-gray-400">Weight</p><p class="text-xl font-bold text-orange-400">${ex.kg} kg</p></div>
                                <div><p class="text-xs text-gray-400">Reps</p><p class="text-xl font-bold">${ex.reps}</p></div>
                                <div><p class="text-xs text-gray-400">Sets</p><p class="text-xl font-bold">${ex.sets}</p></div>
                            </div>
                        </div>
                    `).join('')}
                </div>

                <div class="mt-8 text-center">
                    <p class="font-semibold mb-3">Körde du detta pass idag?</p>
                    <div class="flex justify-center gap-4">
                        <button id="complete-btn" class="bg-green-600 hover:bg-green-700 text-white font-bold py-3 px-6 rounded-lg transition-transform transform hover:scale-105">Yes, I'm Done!</button>
                        <button id="skip-btn" class="bg-yellow-600 hover:bg-yellow-700 text-white font-bold py-3 px-6 rounded-lg transition-transform transform hover:scale-105">Skip this workout</button>
                    </div>
                </div>
            </div>
        `}
    `;
}

// --- GRAFRITNING (Chart.js) ---

function renderDashboardPie(progress) {
    const ctx = document.getElementById('dashboard-progress-pie-chart')?.getContext('2d');
    if (!ctx) return;
    if (dashboardProgressPieChartInstance) dashboardProgressPieChartInstance.destroy();

    const totalVolume = Number(progress.currentWeekVolume ?? Object.values(progress.volumeByType ?? {}).reduce((sum, value) => sum + Number(value || 0), 0));
    const goalVolume = Number(progress.weeklyGoal ?? 12000);
    const colors = { Chest: '#48BB78', Back: '#F56565', Legs: '#4299E1' };

    const labels = Object.keys(progress.volumeByType || {})
        .filter(k => (progress.volumeByType[k] ?? 0) > 0)
        .map(k => k);
    const dataValues = Object.keys(progress.volumeByType || {})
        .filter(k => (progress.volumeByType[k] ?? 0) > 0)
        .map(k => Number(progress.volumeByType[k] || 0));
    const bgColors = labels.map(label => colors[label] || '#4A5568');

    if (progress.remaining > 0) {
        labels.push('Remaining');
        dataValues.push(Number(progress.remaining || 0));
        bgColors.push('#4A5568');
    }

    dashboardProgressPieChartInstance = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{ data: dataValues, backgroundColor: bgColors, borderWidth: 0 }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '72%',
            plugins: {
                legend: { display: true, position: 'bottom', labels: { color: 'white', usePointStyle: true } },
                tooltip: {
                    callbacks: {
                        label: (context) => `${context.label}: ${Number(context.parsed).toLocaleString()} kg`
                    }
                },
                donutCenterText: { total: totalVolume, goal: goalVolume }
            }
        }
    });
}

function renderVolumeChart(labels, datasets) {
    const ctx = document.getElementById('volume-chart')?.getContext('2d');
    if (!ctx) return;

    const recentLabels = labels.length > 12 ? labels.slice(-12) : labels;
    const recentDatasets = datasets.map(ds => ({
        ...ds,
        data: ds.data.length > 12 ? ds.data.slice(-12) : ds.data
    }));

    const maxValue = Math.max(0, ...recentDatasets.flatMap(ds => ds.data));
    const yMax = Math.max(1000, Math.ceil(maxValue / 1000) * 1000);

    if (chartInstance) chartInstance.destroy();
    chartInstance = new Chart(ctx, {
        type: 'line',
        data: { labels: recentLabels, datasets: recentDatasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: { mode: 'nearest', intersect: false },
            plugins: {
                legend: { display: true, position: 'top', labels: { color: 'white' } },
                tooltip: { enabled: false }
            },
            elements: {
                point: { radius: 2, hoverRadius: 4 },
                line: { tension: 0.2, borderWidth: 2, spanGaps: false }
            },
            scales: {
                x: {
                    ticks: {
                        color: 'white',
                        autoSkip: false,
                        maxTicksLimit: 12
                    },
                    grid: { color: 'rgba(255,255,255,0.08)' }
                },
                y: {
                    beginAtZero: true,
                    suggestedMax: yMax,
                    ticks: {
                        color: 'white',
                        callback: (value) => `${Math.round(value / 1000)}k`
                    },
                    grid: { color: 'rgba(255,255,255,0.08)' }
                }
            }
        }
    });
}

function renderSessionsChart(labels, data) {
    const ctx = document.getElementById('sessions-chart')?.getContext('2d');
    if (!ctx) return;
    if (exerciseChartInstance) exerciseChartInstance.destroy();
    exerciseChartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Total Sessions',
                data: data,
                backgroundColor: ['rgba(72, 187, 120, 0.5)', 'rgba(245, 101, 101, 0.5)', 'rgba(66, 153, 225, 0.5)'],
                borderColor: ['#48BB78', '#F56565', '#4299E1'],
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false }, tooltip: { enabled: false } },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: { color: 'white' },
                    grid: { color: 'rgba(255,255,255,0.08)' }
                },
                x: {
                    ticks: { color: 'white' },
                    grid: { color: 'rgba(255,255,255,0.08)' }
                }
            }
        }
    });
}

function renderAdaptionChart(datasets, minDate, maxDate) {
    const ctx = document.getElementById('intensity-difficulty-chart')?.getContext('2d');
    if (!ctx) return;
    if (intensityDifficultyChartInstance) intensityDifficultyChartInstance.destroy();

    intensityDifficultyChartInstance = new Chart(ctx, {
        type: 'line',
        data: { datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'nearest', intersect: false },
            elements: { point: { radius: 2, hoverRadius: 4 }, line: { tension: 0.3, borderWidth: 2 } },
            scales: {
                x: {
                    type: 'time',
                    time: { unit: 'week' },
                    min: minDate,
                    max: maxDate,
                    ticks: { color: 'white', autoSkip: true, maxTicksLimit: 8 },
                    grid: { color: 'rgba(255,255,255,0.08)' }
                },
                y: {
                    min: -1.5,
                    max: 1.5,
                    ticks: { color: 'white' },
                    grid: { color: 'rgba(255,255,255,0.08)' }
                }
            },
            plugins: {
                legend: { position: 'top', labels: { color: 'white' } },
                tooltip: { enabled: false }
            }
        }
    });
}

// --- NOTIFIKATIONER & FEL ---
function showTempNotification(message, type = 'success') {
    const notif = document.getElementById('notification');
    const msg = document.getElementById('notification-message');
    notif.className = `fixed bottom-4 right-4 p-4 rounded-lg text-white ${type === 'success' ? 'bg-green-600' : 'bg-red-600'}`;
    msg.innerText = message;
    notif.classList.remove('hidden');
    setTimeout(() => notif.classList.add('hidden'), 4000);
}

function showError(msg) {
    errorMessage.textContent = msg;
    switchView('error-view');
}