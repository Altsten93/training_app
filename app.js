// --- CONFIGURATION ---
const CONFIG = {
    chest: "https://docs.google.com/spreadsheets/d/e/2PACX-1vTZEwzPvfWQhcYGCjEShCyYoSSelbrQkTI7Mu6hVRgw190wDS0o84OQjTOSWdxje62AJ62bCMOVpSI7/pub?gid=0&single=true&output=csv",
    back: "https://docs.google.com/spreadsheets/d/e/2PACX-1vTZEwzPvfWQhcYGCjEShCyYoSSelbrQkTI7Mu6hVRgw190wDS0o84OQjTOSWdxje62AJ62bCMOVpSI7/pub?gid=1317122870&single=true&output=csv",
    legs: "https://docs.google.com/spreadsheets/d/e/2PACX-1vTZEwzPvfWQhcYGCjEShCyYoSSelbrQkTI7Mu6hVRgw190wDS0o84OQjTOSWdxje62AJ62bCMOVpSI7/pub?gid=1972507766&single=true&output=csv",
    scriptUrl: "https://script.google.com/macros/s/AKfycbygMk5VLSori47VCZf2LvW9HIJgzN93Rg4XArJ6Rc52-xY7vPUn0WYBWQhYyuFAbWS9/exec"
};

// --- DOM ELEMENTS ---
const views = document.querySelectorAll('.view');
const workoutView = document.getElementById('workout-view');
const errorMessage = document.getElementById('error-message');

// --- STATE ---
let allWorkoutsData = [];
let nextWorkoutData = null;
let chartInstance = null;
let exerciseChartInstance = null;
let progressPieChartInstance = null;
let workoutSkipOffset = -1;
let currentWorkoutGroupIndex = 0;

// --- INITIALIZATION ---
document.addEventListener('DOMContentLoaded', handleFetchData);

// --- EVENT LISTENERS ---

document.getElementById('show-workout-btn').addEventListener('click', () => {
    workoutSkipOffset = 0;
    updateWorkoutView();
    switchView('workout-view');
});

document.getElementById('show-stats-btn').addEventListener('click', () => {
    switchView('dashboard-view');
    prepareDashboard();
});

document.getElementById('refresh-data-btn').addEventListener('click', handleFetchData);
document.getElementById('home-from-completion-btn').addEventListener('click', () => location.reload());

workoutView.addEventListener('click', (e) => {
    if (e.target.classList.contains('back-btn')) {
        switchView('home-view');
    } else if (e.target.id === 'complete-btn') {
        markWorkoutComplete();
    } else if (e.target.id === 'skip-btn') {
        skipWorkout();
    }
});

document.querySelectorAll('.back-btn').forEach(btn => {
    btn.addEventListener('click', () => switchView('home-view'));
});

// --- VIEW MANAGEMENT ---

/**
 * Switches the active view.
 * @param {string} viewId - The ID of the view to activate.
 */
function switchView(viewId) {
    views.forEach(view => view.classList.remove('active'));
    document.getElementById(viewId).classList.add('active');
}

// --- DATA FETCHING AND PROCESSING ---

/**
 * Fetches all workout data from the Google Sheets.
 */
async function handleFetchData() {
    try {
        workoutSkipOffset = -1;
        switchView('loader-view');
        const [chestData, backData, legsData] = await Promise.all([
            fetchSheet(CONFIG.chest, 'Chest'),
            fetchSheet(CONFIG.back, 'Back'),
            fetchSheet(CONFIG.legs, 'Legs')
        ]);
        
        allWorkoutsData = [...chestData, ...backData, ...legsData];

        const workoutGroups = [chestData, backData, legsData];
        let lastCompletedDates = [];

        for (const group of workoutGroups) {
            let lastDate = null;
            for (const workout of group) {
                if (workout['Completed_workout']?.toLowerCase() === 'ja' && workout.Datum) {
                    const parts = workout.Datum.split('/');
                    const workoutDate = new Date(parts[2], parts[1] - 1, parts[0]);
                    if (!lastDate || workoutDate > lastDate) {
                        lastDate = workoutDate;
                    }
                }
            }
            lastCompletedDates.push({ group, lastDate });
        }

        lastCompletedDates.sort((a, b) => {
            if (!a.lastDate) return -1; // Groups with no completed workouts first
            if (!b.lastDate) return 1;
            return a.lastDate - b.lastDate;
        });

        const workoutGroupOrder = ['Chest', 'Back', 'Legs'];
        const leastRecentGroup = lastCompletedDates[0].group[0].workoutType;
        currentWorkoutGroupIndex = workoutGroupOrder.indexOf(leastRecentGroup);

        prepareDashboard();
        
        switchView('home-view');
    } catch (error) {
        console.error("Fetch Error:", error);
        showError(error.message || "An unknown error occurred.");
    }
}

/**
 * Fetches and parses a single CSV sheet.
 * @param {string} url - The URL of the CSV file.
 * @param {string} type - The type of workout.
 * @returns {Promise<object[]>} A promise that resolves to an array of workout objects.
 */
async function fetchSheet(url, type) {
    console.log(`Fetching ${type} data from: ${url}`);
    const response = await fetch(`${url}&_=${new Date().getTime()}`);
    if (!response.ok) throw new Error(`Failed to fetch ${type} sheet.`);
    const text = await response.text();
    console.log(`Raw response for ${type}:`, text);
    
    const lines = text.trim().split('\n');
    if (lines.length < 2) return []; 
    
    const headers = lines[0].split(',').map(h => h.trim());
    const data = [];

    for (let i = 1; i < lines.length; i++) {
        const values = lines[i].split(',').map(v => v.trim());
        if (values.length === headers.length) {
            let row = { 
                workoutType: type,
                originalRowIndex: i + 1 // Crucial for updating the correct row
            };
            headers.forEach((header, index) => {
                row[header] = values[index];
            });
            data.push(row);
        }
    }
    console.log(`Parsed ${type} data:`, data);
    return data;
}



function findNextWorkout(workoutGroups, offset = 0) {
    let allWorkouts = [].concat(...workoutGroups);
    let uncompletedWorkouts = allWorkouts.filter(w => w['Completed_workout']?.toLowerCase() === 'nej');

    // If there are no uncompleted workouts, return null
    if (uncompletedWorkouts.length === 0) {
        return null;
    }

    // Create a map to store the last completed date for each workout type
    const lastCompletedDates = {};
    for (const group of workoutGroups) {
        let lastDate = null;
        for (const workout of group) {
            if (workout['Completed_workout']?.toLowerCase() === 'ja' && workout.Datum) {
                const parts = workout.Datum.split('/');
                const workoutDate = new Date(parts[2], parts[1] - 1, parts[0]);
                if (!lastDate || workoutDate > lastDate) {
                    lastDate = workoutDate;
                }
            }
        }
        if(group.length > 0){
            lastCompletedDates[group[0].workoutType] = lastDate;
        }
    }

    // Sort uncompleted workouts
    uncompletedWorkouts.sort((a, b) => {
        const aDate = lastCompletedDates[a.workoutType];
        const bDate = lastCompletedDates[b.workoutType];

        if (!aDate && bDate) return -1;
        if (aDate && !bDate) return 1;
        if (!aDate && !bDate) {
            const typeOrder = { 'Chest': 0, 'Back': 1, 'Legs': 2 };
            if(typeOrder[a.workoutType] !== typeOrder[b.workoutType]){
                return typeOrder[a.workoutType] - typeOrder[b.workoutType];
            }
            return a.originalRowIndex - b.originalRowIndex;
        }
        if (aDate !== bDate) {
            return aDate - bDate;
        }
        return a.originalRowIndex - b.originalRowIndex;
    });

    return uncompletedWorkouts[offset] || null;
}



/**
 * Finds the last completed workout.
 * @param {object[][]} workoutGroups - An array of workout groups.
 * @returns {object|null} The last completed workout object, or null if none are completed.
 */
function findLastCompletedWorkout(workoutGroups) {
    let lastCompleted = null;
    for (const group of workoutGroups) {
        for (const workout of group) {
            if (workout['Completed_workout']?.toLowerCase() === 'ja' && workout.Datum) {
                const parts = workout.Datum.split('/');
                const workoutDate = new Date(parts[2], parts[1] - 1, parts[0]);
                if (!lastCompleted) {
                    lastCompleted = workout;
                } else {
                    const lastCompletedParts = lastCompleted.Datum.split('/');
                    const lastCompletedDate = new Date(lastCompletedParts[2], lastCompletedParts[1] - 1, lastCompletedParts[0]);
                    if (workoutDate > lastCompletedDate) {
                        lastCompleted = workout;
                    }
                }
            }
        }
    }
    return lastCompleted;
}

/**
 * Shows a notification message.
 * @param {string} message - The message to display.
 */
function showNotification(message) {
    const notification = document.getElementById('notification');
    const notificationMessage = document.getElementById('notification-message');

    notificationMessage.textContent = message;
    notification.classList.remove('hidden');

    setTimeout(() => {
        notification.classList.add('hidden');
        location.reload();
    }, 2000); // Hide after 2 seconds and reload
}

/**
 * Marks the current workout as complete and updates the Google Sheet.
 */
async function markWorkoutComplete() {
    const button = document.getElementById('complete-btn');
    button.disabled = true;
    button.textContent = 'Updating...';

    const today = new Date();
    const formattedDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;

    const payload = {
        sheetName: nextWorkoutData.workoutType, // Send the full workoutType for the backend to map
        rowIndex: nextWorkoutData.originalRowIndex,
        date: formattedDate
    };

    try {
        const response = await fetch(CONFIG.scriptUrl, {
            method: 'POST',
            mode: 'cors', // Important for cross-origin requests
            body: JSON.stringify(payload),
        });
        const result = await response.json();
        if (result.status === 'success') {
            showCompletionScreen();
        } else {
            throw new Error(result.message || 'The script returned an error.');
        }
    } catch (error) {
        console.error("Error updating sheet:", error);
        alert(`Failed to update sheet. Error: ${error.message}`);
        button.disabled = false;
        button.textContent = "Yes, I'm Done!";
    }
}


function showCompletionScreen() {
    const weeklyGoal = 12000; // 12000 kg
    const completedWorkouts = allWorkoutsData.filter(w => w['Completed_workout']?.toLowerCase() === 'ja' && w.Datum);
    const currentWeekNumber = getWeekNumber(new Date());
    const currentYear = new Date().getFullYear();

    let currentWeekVolume = 0;
    completedWorkouts.forEach(workout => {
        const dateStr = workout.Datum;
        let date;
        if (dateStr.includes('/')) {
            const parts = dateStr.split('/');
            date = new Date(parts[2], parts[1] - 1, parts[0]);
        } else if (dateStr.includes('-')) {
            date = new Date(dateStr);
        }

        if (date && getWeekNumber(date) === currentWeekNumber && date.getFullYear() === currentYear) {
            for (const key in workout) {
                if (key.endsWith('_volym') && workout[key]) {
                    currentWeekVolume += parseFloat(workout[key]) || 0;
                }
            }
        }
    });

    // Add the volume of the workout that was just completed
    for (const key in nextWorkoutData) {
        if (key.endsWith('_volym') && nextWorkoutData[key]) {
            currentWeekVolume += parseFloat(nextWorkoutData[key]) || 0;
        }
    }

    const percentage = Math.min((currentWeekVolume / weeklyGoal) * 100, 100);
    renderProgressPieChart(percentage, currentWeekVolume);
    switchView('completion-view');

    setTimeout(() => {
        location.reload();
    }, 6000); // Reload after 6 seconds
}

function renderProgressPieChart(completedPercentage, currentWeekVolume) {
    const ctx = document.getElementById('progress-pie-chart').getContext('2d');
    if (progressPieChartInstance) {
        progressPieChartInstance.destroy();
    }

    const data = {
        labels: ['Completed', 'Remaining'],
        datasets: [{
            data: [completedPercentage, 100 - completedPercentage],
            backgroundColor: ['#48BB78', '#4A5568'],
            hoverBackgroundColor: ['#38A169', '#2D3748'],
            borderWidth: 0,
        }]
    };

    const centerText = {
        id: 'centerText',
        afterDraw(chart, args, options) {
            const {ctx, chartArea: {left, right, top, bottom, width, height}} = chart;
            ctx.save();
            
            // Main number
            ctx.font = 'bold 30px Inter';
            ctx.fillStyle = 'white';
            ctx.textAlign = 'center';
            ctx.fillText(`${Math.round(currentWeekVolume)} kg`, width / 2, top + (height / 2) - 10);

            // Percentage
            ctx.font = '16px Inter';
            ctx.fillStyle = 'gray';
            ctx.fillText(`(${completedPercentage.toFixed(1)}%)`, width / 2, top + (height / 2) + 15);
            
            ctx.restore();
        }
    }

    progressPieChartInstance = new Chart(ctx, {
        type: 'doughnut',
        data: data,
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '70%',
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    enabled: false
                }
            }
        },
        plugins: [centerText]
    });
}

// --- UI RENDERING ---

function updateWorkoutView() {
    const workoutGroupOrder = ['Chest', 'Back', 'Legs'];
    const currentGroup = workoutGroupOrder[currentWorkoutGroupIndex];
    const workoutsInGroup = allWorkoutsData.filter(w => w.workoutType === currentGroup && w['Completed_workout']?.toLowerCase() === 'nej');

    nextWorkoutData = workoutsInGroup[0] || null;
    const lastCompletedWorkout = findLastCompletedWorkout([allWorkoutsData.filter(w => w.workoutType === currentGroup)]);
    displayNextWorkout(nextWorkoutData, lastCompletedWorkout);
}

/**This function updates the state-variable with +1 --> pushing us to get the next workout. */
function skipWorkout() {
    currentWorkoutGroupIndex = (currentWorkoutGroupIndex + 1) % 3; // Cycle through 0, 1, 2
    updateWorkoutView();
}

/**
 * Displays the next workout information.
 * @param {object} nextWorkout - The data for the next workout.
 * @param {object} lastCompletedWorkout - The data for the last completed workout.
 */

function displayNextWorkout(nextWorkout, lastCompletedWorkout) {
    console.log('nextWorkout:', nextWorkout);
    workoutView.innerHTML = ''; // Clear the view

    const backBtn = document.createElement('button');
    backBtn.className = 'back-btn mb-4 font-semibold text-blue-400 hover:text-blue-300';
    backBtn.innerHTML = '&larr; Back to Home';
    workoutView.appendChild(backBtn);

    if (!nextWorkout) {
        const allCompleted = document.createElement('div');
        allCompleted.className = 'bg-gray-800 p-6 rounded-2xl text-center';
        allCompleted.innerHTML = `<p class="text-xl font-bold text-green-400">All workouts completed!</p>`;
        workoutView.appendChild(allCompleted);
        return;
    }

    let daysSinceLastWorkout = 0;
    if (lastCompletedWorkout) {
        const parts = lastCompletedWorkout.Datum.split('/');
        const lastDate = new Date(parts[2], parts[1] - 1, parts[0]);
        const today = new Date();
        const diffTime = Math.abs(today - lastDate);
        daysSinceLastWorkout = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
    }

    let funnyMessage = "";
    if (daysSinceLastWorkout > 0) {
        if (daysSinceLastWorkout > 9) {
            funnyMessage = `A wooo, get your fat ass to the gym, it has been ${daysSinceLastWorkout} days ago !!!`;
        } else {
            funnyMessage = `It has been ${daysSinceLastWorkout} days since your last workout.`;
        }
    }

    const container = document.createElement('div');
    container.className = 'bg-gray-800 p-6 rounded-2xl shadow-xl border border-gray-700';

    const title = document.createElement('h2');
    title.className = 'text-2xl font-bold text-center text-blue-300';
    title.textContent = `Next Up: ${nextWorkout.workoutType}`;
    container.appendChild(title);

    const message = document.createElement('p');
    message.className = 'text-center text-gray-400 mb-6';
    message.textContent = funnyMessage;
    container.appendChild(message);

    const exercisesContainer = document.createElement('div');
    exercisesContainer.className = 'space-y-3';

    const exercises = [];
    for (const key in nextWorkout) {
        if (key.endsWith('_KG') && nextWorkout[key]) {
            const baseName = key.replace('_KG', '');
            exercises.push({
                name: baseName.replace(/_/g, ' '),
                kg: nextWorkout[key],
                reps: nextWorkout[`${baseName}_reps`] || 'N/A',
                sets: nextWorkout[`${baseName}_set`] || 'N/A'
            });
        }
    }

    exercises.forEach(ex => {
        const exDiv = document.createElement('div');
        exDiv.className = 'bg-gray-700 p-4 rounded-lg';
        exDiv.innerHTML = `
            <p class="text-lg font-semibold capitalize">${ex.name}</p>
            <div class="grid grid-cols-3 gap-4 text-center mt-2">
                <div><p class="text-xs text-gray-400">Weight</p><p class="text-xl font-bold text-orange-400">${ex.kg} kg</p></div>
                <div><p class="text-xs text-gray-400">Reps</p><p class="text-xl font-bold">${ex.reps}</p></div>
                <div><p class="text-xs text-gray-400">Sets</p><p class="text-xl font-bold">${ex.sets}</p></div>
            </div>`;
        exercisesContainer.appendChild(exDiv);
    });

    container.appendChild(exercisesContainer);

    const completionDiv = document.createElement('div');
    completionDiv.className = 'mt-8 text-center';
    completionDiv.innerHTML = `
        <p class="font-semibold mb-3">Did you complete this workout today?</p>
        <div class="flex justify-center gap-4">
            <button id="complete-btn" class="bg-green-600 hover:bg-green-700 text-white font-bold py-3 px-6 rounded-lg transition-transform transform hover:scale-105">Yes, I'm Done!</button>
            <button id="skip-btn" class="bg-yellow-600 hover:bg-black-700 text-white font-bold py-3 px-6 rounded-lg transition-transform transform hover:scale-105">Skip this workout for now!</button>
        </div>`;
    container.appendChild(completionDiv);

    workoutView.appendChild(container);
}

// --- DASHBOARD ---
function prepareDashboard() {
    const completed = allWorkoutsData.filter(w => w['Completed_workout']?.toLowerCase() === 'ja' && w.Datum);
    const weeklyVolume = {};
    const exerciseCounts = {};
    const firstWorkoutDate = {};

    completed.forEach(workout => {
        let totalVolume = 0;
        const dateStr = workout.Datum;
        let date;
        if (dateStr.includes('/')) {
            const parts = dateStr.split('/');
            date = new Date(parts[2], parts[1] - 1, parts[0]);
        } else if (dateStr.includes('-')) {
            date = new Date(dateStr);
        }

        if (date) {
            const year = date.getFullYear();
            const exerciseType = workout.workoutType;

            // Exercise Counts
            if (!exerciseCounts[exerciseType]) {
                exerciseCounts[exerciseType] = 0;
            }
            exerciseCounts[exerciseType]++;

            // First Workout Date
            if (!firstWorkoutDate[exerciseType] || date < firstWorkoutDate[exerciseType]) {
                firstWorkoutDate[exerciseType] = date;
            }

            for (const key in workout) {
                if (key.endsWith('_volym') && workout[key]) {
                    const volume = parseFloat(workout[key]) || 0;
                    totalVolume += volume;
                }
            }

            if (totalVolume > 0) {
                const weekKey = `${year}-W${getWeekNumber(date)}`;
                if (!weeklyVolume[exerciseType]) {
                    weeklyVolume[exerciseType] = {};
                }
                weeklyVolume[exerciseType][weekKey] = (weeklyVolume[exerciseType][weekKey] || 0) + totalVolume;
            }
        }
    });

    // Prepare data for weekly volume chart
    const allWeeks = [...new Set(Object.values(weeklyVolume).flatMap(Object.keys))].sort();
    const exerciseColors = {
        'Chest': 'green',
        'Back': 'red',
        'Legs': 'blue'
    };

    const datasets = Object.keys(weeklyVolume).map(exercise => {
        const data = allWeeks.map(week => weeklyVolume[exercise][week] || 0);
        return {
            label: `${exercise} Volume (KG)`,
            data: data,
            borderColor: exerciseColors[exercise] || `hsl(${Math.random() * 360}, 70%, 50%)`,
            borderWidth: 1,
            fill: false,
            pointRadius: 0
        };
    });

    renderChart(allWeeks, datasets);

    // Render Sessions Chart
    const exerciseLabels = Object.keys(exerciseCounts);
    const exerciseData = Object.values(exerciseCounts);
    renderSessionsChart(exerciseLabels, exerciseData);

    // Calculate and display average sessions per week
    const averageSessionsDiv = document.getElementById('average-sessions');
    let averageSessionsHTML = '';
    const yearlyExerciseCounts = {};

    completed.forEach(workout => {
        const dateStr = workout.Datum;
        let date;
        if (dateStr.includes('/')) {
            const parts = dateStr.split('/');
            date = new Date(parts[2], parts[1] - 1, parts[0]);
        } else if (dateStr.includes('-')) {
            date = new Date(dateStr);
        }

        if (date) {
            const year = date.getFullYear();
            const exerciseType = workout.workoutType;

            if (!yearlyExerciseCounts[year]) {
                yearlyExerciseCounts[year] = {};
            }
            if (!yearlyExerciseCounts[year][exerciseType]) {
                yearlyExerciseCounts[year][exerciseType] = 0;
            }
            yearlyExerciseCounts[year][exerciseType]++;
        }
    });

    const years = Object.keys(yearlyExerciseCounts).sort().reverse();
    years.forEach(year => {
        averageSessionsHTML += `<div class="mt-4"><h3 class="text-xl font-bold text-center text-blue-300 mb-2">${year}</h3><div class="grid grid-cols-3 gap-4">`;
        for (const exerciseType in yearlyExerciseCounts[year]) {
            const count = yearlyExerciseCounts[year][exerciseType];
            const weeksInYear = (year === new Date().getFullYear().toString()) ? (new Date().getMonth() * 4.345) : 52;
            const average = (count / weeksInYear).toFixed(2);
            averageSessionsHTML += `<div class="text-center"><p class="text-lg font-semibold">${exerciseType}</p><p class="text-2xl font-bold text-blue-400">${average}</p></div>`;
        }
        averageSessionsHTML += `</div></div>`;
    });

    averageSessionsDiv.innerHTML = averageSessionsHTML;
}


/**
 * Renders the weekly volume chart.
 * @param {string[]} labels - The chart labels (weeks).
 * @param {object[]} datasets - The chart datasets.
 */
function renderChart(labels, datasets) {
    const ctx = document.getElementById('volume-chart').getContext('2d');
    if (chartInstance) chartInstance.destroy();
    chartInstance = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets }, 
        options: { scales: { y: { beginAtZero: true } }, responsive: true }
    });
}

/**
 * Renders the total sessions per exercise chart.
 * @param {string[]} labels - The chart labels (exercise types).
 * @param {number[]} data - The chart data (total sessions).
 */
function renderSessionsChart(labels, data) {
    const ctx = document.getElementById('sessions-chart').getContext('2d');
    if (exerciseChartInstance) exerciseChartInstance.destroy();
    exerciseChartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Total Sessions',
                data: data,
                backgroundColor: [
                    'rgba(255, 99, 132, 0.2)',
                    'rgba(54, 162, 235, 0.2)',
                    'rgba(255, 206, 86, 0.2)',
                ],
                borderColor: [
                    'rgba(255, 99, 132, 1)',
                    'rgba(54, 162, 235, 1)',
                    'rgba(255, 206, 86, 1)',
                ],
                borderWidth: 1
            }]
        },
        options: {
            plugins: {
                legend: {
                    display: false
                }
            },
            scales: {
                y: {
                    beginAtZero: true
                }
            }
        }
    });
}

// --- UTILITY FUNCTIONS ---

/**
 * Gets the ISO week number for a given date.
 * @param {Date} d - The date.
 * @returns {number} The week number.
 */
function getWeekNumber(d) {
    d = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
    d.setUTCDate(d.getUTCDate() + 4 - (d.getUTCDay() || 7));
    const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
    return Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
}

/**
 * Displays an error message in the error view.
 * @param {string} message - The error message to display.
 */
function showError(message) {
    errorMessage.textContent = message;
    switchView('error-view');
}
